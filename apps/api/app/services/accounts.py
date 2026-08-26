"""账号维护用例与事务边界。"""

from datetime import datetime
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.security import hash_password, normalize_username, verify_password
from app.models import AuthSession, User
from app.models.user import utc_now


class AccountAlreadyExists(Exception):
    """规范化用户名已被其他账号占用。"""


class AccountNotFound(Exception):
    """目标用户名不存在。"""


class InvalidCurrentPassword(Exception):
    """当前密码校验失败。"""


class PasswordUnchanged(Exception):
    """新密码与当前密码完全相同。"""


PasswordChangeFailureStage = Literal["password_hash", "database"]


class PasswordChangeFailed(Exception):
    """自助改密因内部错误失败，且不携带底层异常或敏感参数。"""

    def __init__(self, stage: PasswordChangeFailureStage) -> None:
        super().__init__(stage)
        self.stage = stage


def create_account(session: Session, username: str, password: str) -> User:
    """创建账号并提交，以数据库唯一约束处理并发重名。

    用户名先规范化，数据库唯一约束是多个 CLI 进程并发创建同名账号时的最终
    防线。仅将可确认的用户名唯一冲突转换为稳定领域异常，其他完整性错误保持
    原因不变。任何校验或写入异常都会回滚本次事务。
    """

    try:
        normalized = normalize_username(username)
        password_hash = hash_password(password)
        user = User(username=normalized, password_hash=password_hash)
        session.add(user)
        session.commit()
        return user
    except IntegrityError as error:
        session.rollback()
        if _is_username_uniqueness_error(error):
            raise AccountAlreadyExists from None
        raise
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
        _apply_password_change(user, password_hash)
        _revoke_active_sessions(session, user.id, now)
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise


def change_own_password(
    session: Session,
    user: User,
    current_password: str,
    new_password: str,
) -> User:
    """验证当前凭据并原子失效该账号的浏览器认证状态。

    当前用户只能来自 access JWT 依赖，调用方不得按用户名或用户 ID 另行选取
    目标账号。密码散列、认证版本和全部 refresh 会话在同一事务中更新；任一
    校验、散列、写入或提交异常都会回滚。MCP Token 具有独立生命周期，不在
    密码变更时撤销。
    """

    if not verify_password(current_password, user.password_hash):
        session.rollback()
        raise InvalidCurrentPassword
    if new_password == current_password:
        session.rollback()
        raise PasswordUnchanged

    password_hash = _hash_password_for_self_service(new_password)
    database_failed = False
    try:
        now = utc_now()
        _apply_password_change(user, password_hash)
        _revoke_active_sessions(session, user.id, now)
        session.commit()
    except SQLAlchemyError:
        _rollback_sensitive_database_failure(session)
        database_failed = True

    if database_failed:
        # 离开 except 后再创建异常，确保敏感底层异常不进入 context 或 cause。
        raise PasswordChangeFailed("database")
    return user


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


def _hash_password_for_self_service(new_password: str) -> str:
    """隔离密码库失败，不让实现异常携带输入或散列细节越过领域边界。"""

    password_hash: str | None = None
    try:
        password_hash = hash_password(new_password)
    except Exception:
        # 密码库是窄外部边界；异常对象只在此块内存活，离开后不再引用。
        pass

    if password_hash is None:
        raise PasswordChangeFailed("password_hash")
    return password_hash


def _rollback_sensitive_database_failure(session: Session) -> None:
    """尽力回滚敏感写事务，SQLAlchemy 回滚错误同样不得向上泄漏。"""

    try:
        session.rollback()
    except SQLAlchemyError:
        # 原始提交或回滚异常都可能含 SQL 参数；上层只接收稳定失败阶段。
        pass


def _apply_password_change(user: User, password_hash: str) -> None:
    """更新密码散列并递增认证版本。

    认证版本使此前签发的 access token 立即失效。调用方必须在同一事务内撤销
    该账号的活跃 refresh 会话，保证密码变更与两类凭据失效要么同时提交，要么
    同时回滚。
    """

    user.password_hash = password_hash
    user.auth_version += 1


def _is_username_uniqueness_error(error: IntegrityError) -> bool:
    """仅识别当前 SQLite 模型可确认的用户名唯一约束冲突。"""

    original = error.orig
    return (
        getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
        and "users.username" in str(original)
    )


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
