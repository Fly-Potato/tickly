from pathlib import Path

from alembic import command
from alembic.config import Config

from app.db.readiness import (
    database_migration_is_current,
    revisions_are_current,
)
from app.db.session import create_engine_for_settings


def make_alembic_config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def make_engine(database_path: Path):
    settings = type(
        "Settings",
        (),
        {"database_url": f"sqlite:///{database_path}"},
    )()
    return create_engine_for_settings(settings)


def test_revision_comparison_is_order_independent() -> None:
    # Alembic 允许多个 heads；readiness 必须比较集合而不是依赖返回顺序。
    assert revisions_are_current(("head_a", "head_b"), ("head_b", "head_a"))
    assert not revisions_are_current(("head_a",), ("head_a", "head_b"))


def test_migrated_database_revision_is_current(tmp_path: Path) -> None:
    database_path = tmp_path / "current.db"
    config = make_alembic_config(database_path)
    command.upgrade(config, "head")
    engine = make_engine(database_path)

    assert database_migration_is_current(engine, config)

    engine.dispose()


def test_empty_database_revision_is_not_current(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.db"
    config = make_alembic_config(database_path)
    engine = make_engine(database_path)

    assert not database_migration_is_current(engine, config)

    engine.dispose()
