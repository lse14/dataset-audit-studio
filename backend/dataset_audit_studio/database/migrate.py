from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from dataset_audit_studio.database.session import Database
from dataset_audit_studio.runtime import PROJECT_ROOT


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    script_location = PROJECT_ROOT / "backend" / "dataset_audit_studio" / "database" / "migrations"
    config.set_main_option("script_location", str(script_location).replace("%", "%%"))
    config.set_main_option("prepend_sys_path", str(PROJECT_ROOT / "backend").replace("%", "%%"))
    return config


def upgrade_database(database: Database, revision: str = "head") -> None:
    config = alembic_config()
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade_database(database: Database, revision: str = "base") -> None:
    config = alembic_config()
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


def check_database_schema(database: Database) -> None:
    config = alembic_config()
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.check(config)


def migration_head() -> str:
    config = alembic_config()
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(config).get_current_head()


def default_database_path() -> Path:
    return PROJECT_ROOT / "data" / "app.db"
