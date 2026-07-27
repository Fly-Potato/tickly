from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.user import utc_now

if TYPE_CHECKING:
    from app.models.user import User


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # 约束放在数据库层，防止绕过 API 校验的脚本写入非法任务数据。
        CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_tasks_title_length"),
        CheckConstraint(
            "priority IN ('none', 'low', 'medium', 'high')", name="ck_tasks_priority"
        ),
        CheckConstraint("notes IS NULL OR length(notes) <= 4000", name="ck_tasks_notes_length"),
        # 列表查询始终按用户过滤，复合索引覆盖常用状态、截止时间和创建时间排序。
        Index("ix_tasks_user_completed", "user_id", "is_completed"),
        Index("ix_tasks_user_due", "user_id", "due_at"),
        Index("ix_tasks_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_completed: Mapped[bool] = mapped_column(nullable=False, default=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    due_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now, onupdate=utc_now)

    user: Mapped["User"] = relationship(back_populates="tasks")
