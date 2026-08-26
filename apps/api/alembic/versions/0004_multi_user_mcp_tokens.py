"""为多账号认证增加 MCP Token 与认证版本。

Revision ID: 0004_multi_user_mcp_tokens
Revises: 0003_add_cancelled_task_status
Create Date: 2026-08-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_multi_user_mcp_tokens"
down_revision: str | Sequence[str] | None = "0003_add_cancelled_task_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 非空默认值让既有账号在增加认证版本时原地回填为首版契约。
    op.add_column(
        "users",
        sa.Column(
            "auth_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        # 数据库只保存不可逆摘要；原始 secret 只允许在创建时返回给调用方。
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "instr(name, char(0)) = 0 AND length(name) BETWEEN 1 AND 64",
            name="ck_mcp_tokens_name_length",
        ),
        sa.CheckConstraint(
            "instr(token_hash, char(0)) = 0 AND length(token_hash) = 64 "
            "AND token_hash = lower(token_hash) "
            "AND token_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_mcp_tokens_hash_format",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])


def downgrade() -> None:
    connection = op.get_bind()
    context = op.get_context()
    is_online_sqlite = connection.dialect.name == "sqlite" and not context.as_sql
    if is_online_sqlite:
        # Python sqlite3 的 legacy transaction control 不会为 DDL 自动 BEGIN；
        # 显式事务保证任一步失败都会恢复完整 0004 schema/data，并允许直接重试。
        connection.exec_driver_sql("BEGIN")
    try:
        # 原生 DROP COLUMN 不重建 users，避免触发其子表级联或暴露临时表中间态。
        op.drop_index("ix_mcp_tokens_user_id", table_name="mcp_tokens")
        op.drop_table("mcp_tokens")
        op.drop_column("users", "auth_version")
    except Exception:
        if is_online_sqlite:
            connection.rollback()
        raise
