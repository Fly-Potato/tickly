from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.user import utc_now

if TYPE_CHECKING:
    from app.models.user import User


class McpToken(Base):
    """持久化 MCP Token 的摘要与生命周期，原始 secret 永不写入数据库。"""

    __tablename__ = "mcp_tokens"
    __table_args__ = (
        CheckConstraint(
            "instr(name, char(0)) = 0 AND length(name) BETWEEN 1 AND 64",
            name="ck_mcp_tokens_name_length",
        ),
        # 仅允许固定格式的 SHA-256 十六进制摘要，避免误将原始 secret 持久化。
        CheckConstraint(
            "instr(token_hash, char(0)) = 0 AND length(token_hash) = 64 "
            "AND token_hash = lower(token_hash) "
            "AND token_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_mcp_tokens_hash_format",
        ),
        UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now)

    user: Mapped["User"] = relationship(back_populates="mcp_tokens")
