from __future__ import annotations

import copy
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.database.base import utc_now
from dataset_audit_studio.database.models import TaskPreset
from dataset_audit_studio.database.session import Database


class TaskPresetError(RuntimeError):
    pass


class TaskPresetNotFound(TaskPresetError):
    pass


class TaskPresetNameConflict(TaskPresetError):
    pass


class TaskPresetVersionConflict(TaskPresetError):
    pass


@dataclass(frozen=True)
class TaskPresetView:
    id: str
    name: str
    components: dict[str, Any]
    profile: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime


class TaskPresetService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_presets(self) -> list[TaskPresetView]:
        with self.database.read_session() as session:
            rows = session.scalars(
                select(TaskPreset).order_by(TaskPreset.name_key, TaskPreset.id)
            ).all()
            return [self._view(row) for row in rows]

    def create(self, *, name: str, components: dict[str, Any], profile: str) -> TaskPresetView:
        cleaned, name_key = self._normalized_name(name)
        with self.database.write_session() as session:
            self._require_available_name(session, name_key)
            preset = TaskPreset(
                name=cleaned,
                name_key=name_key,
                components_json={"profile": profile, "components": copy.deepcopy(components)},
                row_version=1,
            )
            session.add(preset)
            session.flush()
            return self._view(preset)

    def update(
        self,
        preset_id: str,
        *,
        name: str,
        components: dict[str, Any],
        profile: str,
        expected_version: int,
    ) -> TaskPresetView:
        cleaned, name_key = self._normalized_name(name)
        with self.database.write_session() as session:
            preset = self._require(session, preset_id)
            self._require_version(preset, expected_version)
            self._require_available_name(session, name_key, excluding_id=preset.id)
            preset.name = cleaned
            preset.name_key = name_key
            preset.components_json = {"profile": profile, "components": copy.deepcopy(components)}
            preset.row_version += 1
            preset.updated_at = utc_now()
            session.flush()
            return self._view(preset)

    def delete(self, preset_id: str, *, expected_version: int) -> TaskPresetView:
        with self.database.write_session() as session:
            preset = self._require(session, preset_id)
            self._require_version(preset, expected_version)
            deleted = self._view(preset)
            session.delete(preset)
            session.flush()
            return deleted

    @staticmethod
    def _normalized_name(name: str) -> tuple[str, str]:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Task preset name must not be blank")
        if len(cleaned) > 200:
            raise ValueError("Task preset name must not exceed 200 characters")
        name_key = unicodedata.normalize("NFKC", cleaned).casefold()
        return cleaned, name_key

    @staticmethod
    def _require(session: Session, preset_id: str) -> TaskPreset:
        preset = session.get(TaskPreset, preset_id)
        if preset is None:
            raise TaskPresetNotFound(f"Task preset not found: {preset_id}")
        return preset

    @staticmethod
    def _require_version(preset: TaskPreset, expected_version: int) -> None:
        if preset.row_version != expected_version:
            raise TaskPresetVersionConflict(
                f"Task preset version conflict: expected {expected_version}, "
                f"current {preset.row_version}"
            )

    @staticmethod
    def _require_available_name(
        session: Session,
        name_key: str,
        *,
        excluding_id: str | None = None,
    ) -> None:
        statement = select(TaskPreset.id).where(TaskPreset.name_key == name_key)
        if excluding_id is not None:
            statement = statement.where(TaskPreset.id != excluding_id)
        if session.scalar(statement) is not None:
            raise TaskPresetNameConflict("A task preset with this name already exists")

    @staticmethod
    def _view(preset: TaskPreset) -> TaskPresetView:
        stored = preset.components_json if isinstance(preset.components_json, dict) else {}
        envelope = isinstance(stored.get("components"), dict)
        return TaskPresetView(
            id=preset.id,
            name=preset.name,
            components=copy.deepcopy(stored.get("components", stored) if envelope else stored),
            profile=(
                stored.get("profile")
                if envelope and isinstance(stored.get("profile"), str)
                else None
            ),
            row_version=preset.row_version,
            created_at=preset.created_at,
            updated_at=preset.updated_at,
        )
