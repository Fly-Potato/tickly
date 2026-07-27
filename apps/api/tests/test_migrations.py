from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.db.session import create_engine_for_settings


def test_initial_migration_can_upgrade_and_downgrade_file_database(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    # 使用真实临时文件验证 migration，不把内存数据库当作唯一集成环境。
    command.upgrade(config, "head")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": f"sqlite:///{database_path}"})()
    )
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "alembic_version",
        "users",
        "auth_sessions",
        "tasks",
    }

    command.downgrade(config, "base")
    # Alembic 自己的版本表保留，但业务表必须全部回退。
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()
