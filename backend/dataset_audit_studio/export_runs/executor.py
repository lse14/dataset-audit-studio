from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dataset_audit_studio.components.dataset_export.contracts import ExportPlan, PlannedFile
from dataset_audit_studio.core.file_integrity import is_reparse
from dataset_audit_studio.database.base import utc_now
from dataset_audit_studio.database.enums import ExportRunStatus
from dataset_audit_studio.database.models import ExportRun, Task, WorkerLease
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export.tree_publisher import ExportTreePublisher
from dataset_audit_studio.export_runs.errors import ExportRunError
from dataset_audit_studio.export_runs.planner import ExportRunPlanner, RunPlan
from dataset_audit_studio.export_runs.service import ExportRunService
from dataset_audit_studio.export_runs.types import ExportRunView
from dataset_audit_studio.jobs.types import ExportRunToken

_LEASE_SECONDS = 300
_MANIFEST_NAME = "export-run-manifest.json"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ExportRunExecutor:
    """Execute one independently leased copy-only export run."""

    def __init__(
        self,
        database: Database,
        *,
        tree_publisher: ExportTreePublisher | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.database = database
        self.tree_publisher = tree_publisher or ExportTreePublisher()
        self.planner = ExportRunPlanner(database, project_root=project_root)

    def run(self, token: ExportRunToken) -> ExportRunView:
        stage = "planning"
        try:
            run_snapshot = ExportRunService(self.database).get(token.export_run_id)
            output_root = self._output_root(run_snapshot)
            if output_root.exists() and any(output_root.iterdir()):
                stage = "published_tree_verification"
                stage = "published_tree_completion"
                return self._recover_published_tree(token, run_snapshot, output_root)

            planned = self.planner.build(token.export_run_id)
            full_plan, manifest_sha256 = self._plan_with_manifest(run_snapshot, planned)
            run = self._prepare(token, planned, full_plan, manifest_sha256)
            output_root = self._output_root(run)
            staging_root = self._staging_root(output_root, run.id)

            if staging_root.exists():
                if not self._staging_is_owned(run, staging_root):
                    raise ExportRunError(
                        "export_output_changed", "Export staging belongs to an unknown run"
                    )
                self.tree_publisher.assert_staging_ready(output_root, staging_root)
            else:
                self.tree_publisher.validate_roots(Path(self._source_root(token)), output_root)
                self._set_copying(token, staging_root, 0, full_plan, manifest_sha256)
                self.tree_publisher.prepare_directories(
                    output_root,
                    staging_root,
                    full_plan,
                    refuse_nonempty=True,
                )

            next_file = self._next_file(run, full_plan, staging_root)
            stage = "copying"
            self._set_copying(token, staging_root, next_file, full_plan, manifest_sha256)
            for index in range(next_file, len(full_plan.files)):
                self.tree_publisher.write_file(staging_root, full_plan.files[index])
                self._set_copying(token, staging_root, index + 1, full_plan, manifest_sha256)

            stage = "staging_verification"
            self._set_status(token, ExportRunStatus.VERIFYING.value, full_plan)
            self.tree_publisher.verify_tree(staging_root, full_plan)
            stage = "publishing"
            self._set_status(token, ExportRunStatus.PUBLISHING.value, full_plan)
            self.tree_publisher.assert_staging_ready(output_root, staging_root)
            self.tree_publisher.publish_tree(staging_root, output_root)
            self.tree_publisher.verify_tree(output_root, full_plan)
            stage = "completion"
            return self._complete(token, planned, full_plan, manifest_sha256)
        except ExportRunError as error:
            return self._fail(token, error.code, str(error))
        except FileNotFoundError as error:
            return self._fail(token, "export_source_missing", str(error))
        except FileExistsError as error:
            return self._fail(token, "export_output_changed", str(error))
        except OSError as error:
            code = (
                "export_insufficient_space" if "requires" in str(error) else "export_publish_failed"
            )
            return self._fail(token, code, str(error))
        except RuntimeError as error:
            return self._fail(token, self._runtime_error_code(str(error)), str(error))
        except Exception as error:  # noqa: BLE001 - an export run must have terminal state
            return self._fail(
                token,
                "export_internal_error",
                f"{stage}: {str(error) or repr(error)}",
            )

    def _prepare(
        self,
        token: ExportRunToken,
        planned: RunPlan,
        full_plan: ExportPlan,
        manifest_sha256: str,
    ) -> ExportRunView:
        with self.database.write_session() as session:
            run = self._require_lease(session, token)
            if run.input_digest not in {None, planned.plan.input_digest}:
                raise ExportRunError("export_input_changed", "Export plan digest changed")
            checkpoint = dict(run.checkpoint_json)
            stored_manifest_sha256 = checkpoint.get("expected_manifest_sha256")
            if stored_manifest_sha256 is not None and stored_manifest_sha256 != manifest_sha256:
                raise ExportRunError("export_input_changed", "Export manifest plan changed")
            run.input_digest = planned.plan.input_digest
            checkpoint["input_digest"] = planned.plan.input_digest
            checkpoint["expected_manifest_sha256"] = manifest_sha256
            run.checkpoint_json = checkpoint
            run.status = ExportRunStatus.PLANNING.value
            run.progress_total = len(full_plan.files)
            run.bytes_total = sum(file.size_bytes for file in full_plan.files)
            run.file_count = 0
            run.summary_json = planned.summary
            self._heartbeat(session, token, run)
            session.flush()
            return ExportRunService._view(run)

    def _source_root(self, token: ExportRunToken) -> str:
        with self.database.read_session() as session:
            run = session.get(ExportRun, token.export_run_id)
            if run is None:
                raise ExportRunError("export_input_changed", "Export run no longer exists")
            task = session.get(Task, run.task_id)
            if task is None:
                raise ExportRunError("export_input_changed", "Export task no longer exists")
            return task.source_root

    @staticmethod
    def _staging_root(output_root: Path, run_id: str) -> Path:
        return output_root.parent / f".{output_root.name}.export-run-{run_id}.staging"

    @staticmethod
    def _output_root(run: ExportRunView) -> Path:
        configured = Path(run.output_root)
        try:
            resolved = configured.resolve(strict=True)
        except OSError as error:
            raise ExportRunError(
                "export_output_changed", "Export output directory is missing"
            ) from error
        if not resolved.is_dir() or is_reparse(configured) or str(resolved) != run.output_root:
            raise ExportRunError("export_output_changed", "Export output directory changed")
        return resolved

    @staticmethod
    def _next_file(run: ExportRunView, plan: ExportPlan, staging_root: Path) -> int:
        checkpoint = run.checkpoint
        if checkpoint.get("input_digest") not in {None, plan.input_digest}:
            raise ExportRunError("export_input_changed", "Export checkpoint digest changed")
        value = checkpoint.get("next_file", 0)
        if not isinstance(value, int) or not 0 <= value <= len(plan.files):
            raise ExportRunError("export_input_changed", "Export checkpoint cursor is invalid")
        if checkpoint.get("staging_root") not in {None, str(staging_root)}:
            raise ExportRunError("export_output_changed", "Export checkpoint staging path changed")
        return value

    def _set_copying(
        self,
        token: ExportRunToken,
        staging_root: Path,
        next_file: int,
        plan: ExportPlan,
        manifest_sha256: str,
    ) -> None:
        with self.database.write_session() as session:
            run = self._require_lease(session, token)
            checkpoint = dict(run.checkpoint_json)
            if (
                checkpoint.get("input_digest") != plan.input_digest
                or checkpoint.get("expected_manifest_sha256") != manifest_sha256
            ):
                raise ExportRunError("export_input_changed", "Export manifest checkpoint changed")
            run.status = ExportRunStatus.COPYING.value
            run.checkpoint_json = {
                "input_digest": plan.input_digest,
                "expected_manifest_sha256": manifest_sha256,
                "staging_root": str(staging_root),
                "staging_owner": run.id,
                "next_file": next_file,
            }
            run.progress_current = next_file
            run.bytes_current = sum(file.size_bytes for file in plan.files[:next_file])
            run.file_count = next_file
            run.progress_total = len(plan.files)
            run.bytes_total = sum(file.size_bytes for file in plan.files)
            self._heartbeat(session, token, run)

    def _set_status(self, token: ExportRunToken, status: str, plan: ExportPlan) -> None:
        with self.database.write_session() as session:
            run = self._require_lease(session, token)
            run.status = status
            run.progress_current = len(plan.files)
            run.progress_total = len(plan.files)
            run.bytes_current = sum(file.size_bytes for file in plan.files)
            run.bytes_total = run.bytes_current
            run.file_count = len(plan.files)
            self._heartbeat(session, token, run)

    def _complete(
        self,
        token: ExportRunToken,
        planned: RunPlan,
        plan: ExportPlan,
        manifest_sha256: str,
    ) -> ExportRunView:
        with self.database.write_session() as session:
            run = self._require_lease(session, token)
            output_root = Path(run.output_root).resolve(strict=True)
            manifest_path = output_root / _MANIFEST_NAME
            if (
                not manifest_path.is_file()
                or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_sha256
            ):
                raise ExportRunError("export_verification_failed", "Export manifest changed")
            run.status = ExportRunStatus.COMPLETED.value
            run.progress_current = len(plan.files)
            run.progress_total = len(plan.files)
            run.bytes_current = sum(file.size_bytes for file in plan.files)
            run.bytes_total = run.bytes_current
            run.file_count = len(plan.files)
            run.manifest_path = str(manifest_path)
            run.manifest_sha256 = manifest_sha256
            run.summary_json = planned.summary
            run.error_code = None
            run.error_message = None
            run.completed_at = utc_now()
            lease = session.get(WorkerLease, 1)
            assert lease is not None
            session.delete(lease)
            session.flush()
            return ExportRunService._view(run)

    def _fail(self, token: ExportRunToken, code: str, message: str) -> ExportRunView:
        with self.database.write_session() as session:
            run = session.get(ExportRun, token.export_run_id)
            lease = session.get(WorkerLease, 1)
            if (
                run is None
                or lease is None
                or lease.export_run_id != token.export_run_id
                or lease.owner != token.owner
                or lease.execution_epoch != token.execution_epoch
            ):
                raise ExportRunError("export_internal_error", message)
            if _aware(lease.expires_at) <= _aware(utc_now()):
                run.status = ExportRunStatus.QUEUED.value
                run.execution_epoch += 1
                run.error_code = None
                run.error_message = None
                run.updated_at = utc_now()
                session.delete(lease)
                session.flush()
                return ExportRunService._view(run)
            run.status = ExportRunStatus.FAILED.value
            run.error_code = code[:80]
            run.error_message = message[:4000]
            run.completed_at = utc_now()
            session.delete(lease)
            session.flush()
            result = ExportRunService._view(run)
        self._cleanup_owned_staging(result)
        return result

    def _recover_published_tree(
        self,
        token: ExportRunToken,
        run: ExportRunView,
        output_root: Path,
    ) -> ExportRunView:
        manifest_path = output_root / _MANIFEST_NAME
        try:
            content = manifest_path.read_bytes()
            manifest_sha256 = hashlib.sha256(content).hexdigest()
            expected_manifest_sha256 = run.checkpoint.get("expected_manifest_sha256")
            if (
                not self._is_manifest_sha256(expected_manifest_sha256)
                or expected_manifest_sha256 != manifest_sha256
            ):
                raise ValueError("Export manifest identity changed")
            payload = json.loads(content)
            plan, summary = self._published_manifest_plan(run, payload, content)
            self.tree_publisher.verify_tree(output_root, plan)
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExportRunError("export_output_changed", str(error)) from error
        return self._complete(
            token,
            RunPlan(plan=plan, summary=summary),
            plan,
            manifest_sha256,
        )

    @staticmethod
    def _published_manifest_plan(
        run: ExportRunView,
        payload: object,
        content: bytes,
    ) -> tuple[ExportPlan, dict]:
        if not isinstance(payload, dict):
            raise ValueError("Export manifest is invalid")
        expected = {
            "schema": "export.run.v3",
            "export_run_id": run.id,
            "task_id": run.task_id,
            "task_config_revision": run.task_config_revision,
            "config_hash": run.config_hash,
            "selection_version": run.selection_version,
            "minimum_resolution": run.minimum_resolution,
            "aesthetic_minimum": run.aesthetic_minimum,
            "minimum_folder_images": run.minimum_folder_images,
            "add_repeat_prefix": run.add_repeat_prefix,
            "sample_seen_mode": run.sample_seen_mode,
            "sample_seen_target": run.sample_seen_target,
            "preview_digest": run.preview_digest,
            "input_digest": run.input_digest,
            "settings": run.settings,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError("Export manifest does not match the run snapshot")
        summary = payload.get("summary")
        files = payload.get("files")
        if not isinstance(summary, dict) or not isinstance(files, list):
            raise ValueError("Export manifest is incomplete")
        planned_files: list[PlannedFile] = []
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Export manifest file entry is invalid")
            path = item.get("path")
            digest = item.get("sha256")
            size = item.get("size")
            kind = item.get("kind")
            if (
                not isinstance(path, str)
                or not path
                or Path(path).is_absolute()
                or ".." in Path(path).parts
                or not isinstance(digest, str)
                or len(digest) != 64
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or not isinstance(kind, str)
            ):
                raise ValueError("Export manifest file entry is invalid")
            planned_files.append(
                PlannedFile(
                    destination_relative=path,
                    sha256=digest,
                    size_bytes=size,
                    kind=kind,
                )
            )
        manifest = PlannedFile(
            destination_relative=_MANIFEST_NAME,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            kind="manifest",
            content=content,
        )
        return (
            ExportPlan(
                files=tuple(
                    sorted(
                        (*planned_files, manifest),
                        key=lambda item: item.destination_relative,
                    )
                ),
                datasets=(),
                latent_records=(),
                input_digest=run.input_digest or "",
            ),
            summary,
        )

    def _cleanup_owned_staging(self, run: ExportRunView) -> None:
        try:
            output_root = Path(run.output_root).resolve(strict=False)
            staging_root = self._staging_root(output_root, run.id)
            if (
                staging_root.parent != output_root.parent
                or staging_root.name != f".{output_root.name}.export-run-{run.id}.staging"
                or not self._staging_is_owned(run, staging_root)
                or not staging_root.exists()
                or is_reparse(staging_root)
                or not staging_root.is_dir()
            ):
                return
            shutil.rmtree(staging_root)
        except OSError:
            # The terminal failure remains durable even if a run-owned temporary
            # directory cannot be removed immediately (for example, a Windows lock).
            return

    @staticmethod
    def _staging_is_owned(run: ExportRunView, staging_root: Path) -> bool:
        checkpoint = run.checkpoint
        return (
            checkpoint.get("staging_root") == str(staging_root)
            and checkpoint.get("staging_owner") == run.id
        )

    @staticmethod
    def _is_manifest_sha256(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @staticmethod
    def _runtime_error_code(message: str) -> str:
        lowered = message.casefold()
        if "sha-256 changed" in lowered:
            return "export_source_hash_mismatch"
        if "source image changed" in lowered or "missing" in lowered:
            return "export_source_missing"
        if "collid" in lowered:
            return "export_collision"
        if "verification" in lowered or "unexpected file" in lowered:
            return "export_verification_failed"
        if "output" in lowered or "staging" in lowered:
            return "export_output_changed"
        return "export_internal_error"

    @staticmethod
    def _heartbeat(session, token: ExportRunToken, run: ExportRun) -> None:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        now = utc_now()
        lease.heartbeat_at = now
        lease.expires_at = now + timedelta(seconds=_LEASE_SECONDS)
        run.updated_at = now

    @staticmethod
    def _require_lease(session, token: ExportRunToken) -> ExportRun:
        run = session.get(ExportRun, token.export_run_id)
        lease = session.get(WorkerLease, 1)
        if (
            run is None
            or lease is None
            or lease.export_run_id != token.export_run_id
            or lease.owner != token.owner
            or lease.execution_epoch != token.execution_epoch
            or run.execution_epoch != token.execution_epoch
            or _aware(lease.expires_at) <= _aware(utc_now())
        ):
            raise ExportRunError("export_internal_error", "Export worker lease is stale")
        return run

    @staticmethod
    def _plan_with_manifest(run: ExportRunView, planned: RunPlan) -> tuple[ExportPlan, str]:
        payload = {
            "schema": "export.run.v3",
            "export_run_id": run.id,
            "task_id": run.task_id,
            "task_config_revision": run.task_config_revision,
            "config_hash": run.config_hash,
            "selection_version": run.selection_version,
            "minimum_resolution": run.minimum_resolution,
            "aesthetic_minimum": run.aesthetic_minimum,
            "minimum_folder_images": run.minimum_folder_images,
            "add_repeat_prefix": run.add_repeat_prefix,
            "sample_seen_mode": run.sample_seen_mode,
            "sample_seen_target": run.sample_seen_target,
            "preview_digest": run.preview_digest,
            "input_digest": planned.plan.input_digest,
            "settings": dict(run.settings),
            "summary": planned.summary,
            "files": [
                {
                    "path": file.destination_relative,
                    "sha256": file.sha256,
                    "size": file.size_bytes,
                    "kind": file.kind,
                }
                for file in planned.plan.files
            ],
        }
        content = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        digest = hashlib.sha256(content).hexdigest()
        manifest = PlannedFile(
            destination_relative=_MANIFEST_NAME,
            sha256=digest,
            size_bytes=len(content),
            kind="manifest",
            content=content,
        )
        files = tuple(
            sorted(
                (*planned.plan.files, manifest),
                key=lambda item: item.destination_relative,
            )
        )
        return (
            ExportPlan(
                files=files,
                datasets=planned.plan.datasets,
                latent_records=planned.plan.latent_records,
                input_digest=planned.plan.input_digest,
                aesthetic_bin_plan=planned.plan.aesthetic_bin_plan,
            ),
            digest,
        )
