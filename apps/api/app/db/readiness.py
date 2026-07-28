from collections.abc import Iterable

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text


def revisions_are_current(
    current_heads: Iterable[str],
    expected_heads: Iterable[str],
) -> bool:
    """比较数据库与代码中的 migration heads。

    Alembic migration 可能存在多个分支，集合比较可以避免顺序变化导致误判。
    """

    return set(current_heads) == set(expected_heads)


def database_migration_is_current(
    database_engine: Engine,
    alembic_config: Config,
) -> bool:
    """检查数据库可访问性，并判断当前 revisions 是否等于代码 heads。

    连接或查询失败由 SQLAlchemy 异常表达；revision 不一致返回 False。
    本函数只读取状态，不执行 migration，也不修改业务 schema。
    """

    script_directory = ScriptDirectory.from_config(alembic_config)
    with database_engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        migration_context = MigrationContext.configure(connection)
        return revisions_are_current(
            migration_context.get_current_heads(),
            script_directory.get_heads(),
        )
