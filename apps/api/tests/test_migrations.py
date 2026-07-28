from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect

from app.core.config import API_ROOT
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


def test_migration_uses_database_url_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "environment.db"
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "TICKLY_DATABASE_URL",
        f"sqlite:///{database_path}",
    )
    config = Config(str(API_ROOT / "alembic.ini"))

    # 容器 migration 必须服从环境配置，不能写入镜像内的开发默认路径。
    command.upgrade(config, "head")

    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": f"sqlite:///{database_path}"})()
    )
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "users",
        "auth_sessions",
        "tasks",
    }
    engine.dispose()
