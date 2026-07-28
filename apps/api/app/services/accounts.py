"""唯一账号的 CLI 用例与事务边界。"""

from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.security import hash_password, normalize_username
from app.models import AuthSession, User
from app.models.user import utc_now


class AccountAlreadyExists(Exception):
    """数据库已经存在账号，禁止创建第二个账号。"""


class AccountNotFound(Exception):
    """目标用户名不存在。"""


def create_account(session: Session, username: str, password: str) -> User:
    """创建唯一账号并提交。

    SQLite 的普通读事务不能阻止两个 CLI 进程同时读到零账号，因此在计数前使用
    ``BEGIN IMMEDIATE`` 获取写锁。任何校验、锁或写入异常都会回滚本次事务。
    """

    try:
        normalized = normalize_username(username)
        password_hash = hash_password(password)
        session.execute(text("BEGIN IMMEDIATE"))
        existing_count = session.scalar(select(func.count()).select_from(User))
        if existing_count:
            raise AccountAlreadyExists

        user = User(username=normalized, password_hash=password_hash)
        session.add(user)
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def change_password(session: Session, username: str, password: str) -> User:
    """在更新密码散列的同一事务内撤销该账号的所有活跃会话。"""

    try:
        normalized = normalize_username(username)
        password_hash = hash_password(password)
        user = _find_account(session, normalized)
        now = utc_now()
        user.password_hash = password_hash
        _revoke_active_sessions(session, user.id, now)
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def deactivate_account(session: Session, username: str) -> User:
    """停用账号并在同一事务内撤销全部活跃会话。"""

    try:
        normalized = normalize_username(username)
        user = _find_account(session, normalized)
        now = utc_now()
        user.is_active = False
        _revoke_active_sessions(session, user.id, now)
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def revoke_all_sessions(session: Session, username: str) -> int:
    """撤销账号当前全部活跃会话，返回实际更新的行数。"""

    try:
        normalized = normalize_username(username)
        user = _find_account(session, normalized)
        revoked_count = _revoke_active_sessions(session, user.id, utc_now())
        session.commit()
        return revoked_count
    except Exception:
        session.rollback()
        raise


def _find_account(session: Session, normalized_username: str) -> User:
    user = session.scalar(select(User).where(User.username == normalized_username))
    if user is None:
        raise AccountNotFound
    return user


def _revoke_active_sessions(
    session: Session, user_id: str, revoked_at: datetime
) -> int:
    result = session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
    )
    return result.rowcount
