from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
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
        CheckConstraint("serial > 0", name="ck_tasks_serial_positive"),
        CheckConstraint(
            "length(description) BETWEEN 1 AND 4000",
            name="ck_tasks_description_length",
        ),
        CheckConstraint("length(topic) BETWEEN 1 AND 100", name="ck_tasks_topic_length"),
        CheckConstraint(
            "status IN ('new', 'in_progress', 'completed')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('low', 'medium', 'high')",
            name="ck_tasks_priority",
        ),
        UniqueConstraint("user_id", "serial", name="uq_tasks_user_serial"),
        # 列表查询始终按账号过滤，复合索引覆盖筛选、父子组装和稳定排序。
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_topic", "user_id", "topic"),
        Index("ix_tasks_user_parent", "user_id", "parent_id"),
        Index("ix_tasks_user_due", "user_id", "due_at"),
        Index("ix_tasks_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    serial: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    due_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # 数据库只保证引用完整性和删除父任务后提升子任务；一层父子和同账号规则由 service 校验。
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now, onupdate=utc_now)

    user: Mapped["User"] = relationship(back_populates="tasks")
    parent: Mapped["Task | None"] = relationship(
        back_populates="children", remote_side="Task.id"
    )
    children: Mapped[list["Task"]] = relationship(
        back_populates="parent", passive_deletes=True
    )
