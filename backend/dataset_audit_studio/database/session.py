from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from dataset_audit_studio.runtime import require_project_path


class Database:
    def __init__(self, path: Path, *, enforce_project_boundary: bool = True) -> None:
        self.path = path.expanduser().resolve(strict=False)
        if enforce_project_boundary:
            require_project_path(self.path, "database path")
        self.path.parent.mkdir(parents=True, exist_ok=True)

        url = URL.create("sqlite+pysqlite", database=str(self.path))
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._configure_sqlite(self.engine)

    @staticmethod
    def _configure_sqlite(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_pragmas(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

    @contextmanager
    def read_session(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session

    @contextmanager
    def write_session(self) -> Iterator[Session]:
        with self._sessions() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def diagnostics(self) -> dict[str, object]:
        with self.engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
        return {
            "path": str(self.path),
            "journal_mode": str(journal_mode).lower(),
            "foreign_keys": bool(foreign_keys),
            "busy_timeout_ms": int(busy_timeout),
        }

    def dispose(self) -> None:
        self.engine.dispose()
