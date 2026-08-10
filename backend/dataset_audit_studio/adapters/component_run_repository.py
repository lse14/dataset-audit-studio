from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.core.component_contracts import ResolvedComponent
from dataset_audit_studio.core.component_runs import ComponentRunView
from dataset_audit_studio.database.base import utc_now
from dataset_audit_studio.database.enums import ComponentRunState
from dataset_audit_studio.database.models import ComponentRun, PhaseCheckpoint
from dataset_audit_studio.jobs.types import TaskView


def _canonical(value: dict[str, Any]) -> tuple[dict[str, Any], str]:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return json.loads(serialized), hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ComponentRunRepository:
    def sync_plan(
        self,
        session: Session,
        *,
        task: TaskView,
        resolved: Sequence[ResolvedComponent],
        phase_by_component: Mapping[str, str],
    ) -> tuple[ComponentRunView, ...]:
        for item in resolved:
            manifest = item.definition.manifest
            try:
                phase = phase_by_component[manifest.id]
            except KeyError as error:
                raise ValueError(f"Component has no execution phase: {manifest.id}") from error
            normalized, digest = _canonical(
                {
                    "component_id": item.config.component_id,
                    "enabled": item.config.enabled,
                    "config": item.config.config,
                }
            )
            row = session.scalar(
                select(ComponentRun).where(
                    ComponentRun.task_id == task.id,
                    ComponentRun.component_id == manifest.id,
                    ComponentRun.config_hash == task.config_hash,
                )
            )
            model_ids = list(item.definition.model_ids(item.config))
            dependencies = list(item.dependency_ids)
            if row is None:
                session.add(
                    ComponentRun(
                        task_id=task.id,
                        component_id=manifest.id,
                        component_version=manifest.version,
                        phase=phase,
                        phase_order=manifest.phase_order,
                        execution=manifest.execution,
                        status=ComponentRunState.PENDING.value,
                        config_hash=task.config_hash,
                        config_digest=digest,
                        normalized_config_json=normalized,
                        dependency_ids_json=dependencies,
                        model_ids_json=model_ids,
                        checkpoint_json={},
                        completed_items=0,
                        total_items=None,
                        auto_enabled=item.auto_enabled,
                    )
                )
                continue
            identity = (
                row.component_version,
                row.phase,
                row.phase_order,
                row.execution,
                row.config_digest,
                row.normalized_config_json,
                row.dependency_ids_json,
                row.model_ids_json,
                row.auto_enabled,
            )
            expected = (
                manifest.version,
                phase,
                manifest.phase_order,
                manifest.execution,
                digest,
                normalized,
                dependencies,
                model_ids,
                item.auto_enabled,
            )
            if identity != expected:
                raise RuntimeError(
                    f"Persisted component plan changed for {manifest.id} and {task.config_hash}"
                )
        session.flush()
        return self.list_for_config(
            session,
            task_id=task.id,
            config_hash=task.config_hash,
        )

    def list_for_config(
        self,
        session: Session,
        *,
        task_id: str,
        config_hash: str,
    ) -> tuple[ComponentRunView, ...]:
        rows = session.scalars(
            select(ComponentRun)
            .where(
                ComponentRun.task_id == task_id,
                ComponentRun.config_hash == config_hash,
            )
            .order_by(ComponentRun.phase_order, ComponentRun.component_id)
        ).all()
        return tuple(self._view(row) for row in rows)

    @staticmethod
    def mark_running(
        session: Session,
        *,
        task_id: str,
        config_hash: str,
        component_ids: Sequence[str],
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        for row in ComponentRunRepository._rows(
            session,
            task_id=task_id,
            config_hash=config_hash,
            component_ids=component_ids,
        ):
            if row.status == ComponentRunState.COMPLETED.value:
                continue
            row.status = ComponentRunState.RUNNING.value
            row.started_at = row.started_at or timestamp
            row.finished_at = None
            row.error_code = None
            row.error_message = None

    @staticmethod
    def mark_status(
        session: Session,
        *,
        task_id: str,
        config_hash: str,
        component_ids: Sequence[str],
        status: ComponentRunState,
        error_code: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        terminal = {
            ComponentRunState.COMPLETED,
            ComponentRunState.TERMINATED,
            ComponentRunState.FAILED,
        }
        for row in ComponentRunRepository._rows(
            session,
            task_id=task_id,
            config_hash=config_hash,
            component_ids=component_ids,
        ):
            if (
                row.status == ComponentRunState.COMPLETED.value
                and status is not ComponentRunState.COMPLETED
            ):
                continue
            row.status = status.value
            row.started_at = row.started_at or timestamp
            row.finished_at = timestamp if status in terminal else None
            row.error_code = error_code
            row.error_message = error_message

    @staticmethod
    def mark_before_order_completed(
        session: Session,
        *,
        task_id: str,
        config_hash: str,
        phase_order: int,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        rows = session.scalars(
            select(ComponentRun).where(
                ComponentRun.task_id == task_id,
                ComponentRun.config_hash == config_hash,
                ComponentRun.phase_order < phase_order,
                ComponentRun.status.in_(
                    (
                        ComponentRunState.PENDING.value,
                        ComponentRunState.RUNNING.value,
                        ComponentRunState.PAUSED.value,
                    )
                ),
            )
        ).all()
        for row in rows:
            row.status = ComponentRunState.COMPLETED.value
            row.started_at = row.started_at or timestamp
            row.finished_at = timestamp

    @staticmethod
    def reconcile_phase_checkpoints(
        session: Session,
        *,
        task_id: str,
        config_hash: str,
        phase: str,
    ) -> None:
        checkpoints = session.scalars(
            select(PhaseCheckpoint)
            .where(
                PhaseCheckpoint.task_id == task_id,
                PhaseCheckpoint.config_hash == config_hash,
                PhaseCheckpoint.phase == phase,
            )
            .order_by(PhaseCheckpoint.batch_index)
        ).all()
        latest: dict[str, PhaseCheckpoint] = {}
        for checkpoint in checkpoints:
            component_id = checkpoint.cursor_json.get("component_id")
            if isinstance(component_id, str):
                latest[component_id] = checkpoint
        if not latest:
            return
        rows = ComponentRunRepository._rows(
            session,
            task_id=task_id,
            config_hash=config_hash,
            component_ids=tuple(latest),
        )
        now = utc_now()
        for row in rows:
            checkpoint = latest[row.component_id]
            cursor = checkpoint.cursor_json
            row.checkpoint_json = cursor
            row.input_digest = ComponentRunRepository._digest(
                cursor,
                "identity_digest",
                "input_digest",
            )
            row.model_digest = ComponentRunRepository._model_digest(cursor)
            row.completed_items = ComponentRunRepository._completed(cursor)
            if cursor.get("component_complete") is True:
                row.status = ComponentRunState.COMPLETED.value
                row.finished_at = row.finished_at or now

    @staticmethod
    def apply_batch_checkpoint(
        session: Session,
        *,
        task_id: str,
        config_hash: str,
        cursor: dict[str, Any],
        control_state: str,
        now: datetime,
    ) -> None:
        component_id = cursor.get("component_id")
        if not isinstance(component_id, str):
            return
        row = session.scalar(
            select(ComponentRun).where(
                ComponentRun.task_id == task_id,
                ComponentRun.config_hash == config_hash,
                ComponentRun.component_id == component_id,
            )
        )
        if row is None:
            return
        normalized, _ = _canonical(cursor)
        row.checkpoint_json = normalized
        row.input_digest = ComponentRunRepository._digest(
            normalized,
            "identity_digest",
            "input_digest",
        )
        row.model_digest = ComponentRunRepository._model_digest(normalized)
        row.completed_items = ComponentRunRepository._completed(normalized)
        row.started_at = row.started_at or now
        if normalized.get("component_complete") is True:
            row.status = ComponentRunState.COMPLETED.value
            row.finished_at = now
        elif control_state == "paused":
            row.status = ComponentRunState.PAUSED.value
            row.finished_at = None
        elif control_state == "terminated":
            row.status = ComponentRunState.TERMINATED.value
            row.finished_at = now
        else:
            row.status = ComponentRunState.RUNNING.value
            row.finished_at = None

    @staticmethod
    def _rows(
        session: Session,
        *,
        task_id: str,
        config_hash: str,
        component_ids: Sequence[str],
    ) -> list[ComponentRun]:
        if not component_ids:
            return []
        return list(
            session.scalars(
                select(ComponentRun).where(
                    ComponentRun.task_id == task_id,
                    ComponentRun.config_hash == config_hash,
                    ComponentRun.component_id.in_(tuple(component_ids)),
                )
            ).all()
        )

    @staticmethod
    def _digest(cursor: dict, *keys: str) -> str | None:
        for key in keys:
            value = cursor.get(key)
            if isinstance(value, str) and len(value) == 64:
                return value
        return None

    @staticmethod
    def _model_digest(cursor: dict) -> str | None:
        direct = ComponentRunRepository._digest(cursor, "model_digest")
        if direct is not None:
            return direct
        for key, value in cursor.items():
            if key.endswith("model_digest") and isinstance(value, str) and len(value) == 64:
                return value
        return None

    @staticmethod
    def _completed(cursor: dict) -> int:
        for key in (
            "next_index",
            "next_file",
            "next_dataset",
            "processed_samples",
            "inferred_samples",
        ):
            value = cursor.get(key)
            if isinstance(value, int) and value >= 0:
                return value
        return 0

    @staticmethod
    def _view(row: ComponentRun) -> ComponentRunView:
        return ComponentRunView(
            component_id=row.component_id,
            component_version=row.component_version,
            phase=row.phase,
            phase_order=row.phase_order,
            execution=row.execution,
            status=row.status,
            config_hash=row.config_hash,
            config_digest=row.config_digest,
            input_digest=row.input_digest,
            model_digest=row.model_digest,
            normalized_config=row.normalized_config_json,
            dependency_ids=tuple(row.dependency_ids_json),
            model_ids=tuple(row.model_ids_json),
            checkpoint=row.checkpoint_json,
            completed_items=row.completed_items,
            total_items=row.total_items,
            auto_enabled=row.auto_enabled,
            error_code=row.error_code,
            error_message=row.error_message,
            started_at=row.started_at,
            finished_at=row.finished_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
