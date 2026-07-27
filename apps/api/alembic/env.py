from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_engine_for_settings
from app import models  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url")
    return configured_url or Settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = Settings(database_url=database_url(), _env_file=None)
    connectable = create_engine_for_settings(settings, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
