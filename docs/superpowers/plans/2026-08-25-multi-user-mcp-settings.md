# Tickly Multi-User MCP and Account Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员可通过现有 CLI 创建多个账号，并让每个 Web 用户修改自己的密码、管理多个用户级 MCP Token，且所有 MCP 工具严格绑定 Token 所属用户。

**Architecture:** API 持有用户、Token 摘要和授权判断，MCP 不访问 SQLite，只在传输层向 API 验证 Bearer Token 并在工具调用时透传同一 Token。Web JWT、用户级 MCP Token 和内部任务路由保持三个独立认证域，现有任务 service 继续使用 `user.id` 做所有权隔离。

**Tech Stack:** Python 3.13、FastAPI 0.140、SQLAlchemy 2、Alembic、pwdlib/Argon2、PyJWT、MCP Python SDK v2、HTTPX、React 19、TypeScript 6、Vite 8、Tailwind CSS 4、Base UI、Vitest、pytest、Docker Compose、Caddy。

**Source design:** `docs/superpowers/specs/2026-08-25-multi-user-mcp-settings-design.md`

---

## Execution rules

- 从仓库根目录执行命令，优先使用 `mise exec -- ...`。
- 严格按 TDD 顺序执行每个任务：先写失败测试、确认 RED、实现最小代码、确认 GREEN。
- 测试中的注释、docstring、fixture 说明和断言说明必须使用中文；生产关键安全和事务边界使用中文注释。
- 原始密码、Web Token、MCP Token、摘要、Authorization Header 和请求正文不得进入日志、测试失败消息或命令输出。
- 本计划列出建议提交点，但只有用户明确说“提交”时才执行 `git add`/`git commit`；否则跳过 Commit 步骤并保留工作树改动。
- 当前无关的 `apps/.pytest-release/` 必须保持未暂存、未修改。

## File map

### API database and domain

- Create `apps/api/alembic/versions/0004_multi_user_mcp_tokens.py`: add `users.auth_version` and `mcp_tokens`.
- Create `apps/api/app/models/mcp_token.py`: ORM model and user relationship.
- Modify `apps/api/app/models/user.py`: add `auth_version` and `mcp_tokens` relationship.
- Modify `apps/api/app/models/__init__.py`: export `McpToken`.
- Modify `apps/api/app/services/accounts.py`: allow multiple unique accounts and centralize password rotation.
- Modify `apps/api/app/core/security.py`: version Web access tokens.
- Modify `apps/api/app/services/auth.py`: issue and validate the access-token version.
- Create `apps/api/app/services/mcp_tokens.py`: generate, list, revoke and authenticate user tokens.

### API HTTP contracts

- Create `apps/api/app/schemas/account.py` and `apps/api/app/api/routes/account.py`: self-service password change.
- Create `apps/api/app/api/auth_cookies.py`: shared refresh-cookie helpers.
- Create `apps/api/app/schemas/mcp_tokens.py` and `apps/api/app/api/routes/mcp_tokens.py`: Web token management.
- Create `apps/api/app/api/routes/mcp_auth.py`: internal transport verification endpoint.
- Modify `apps/api/app/api/mcp_dependencies.py`: resolve the user from a database token.
- Modify `apps/api/app/api/router.py`, `apps/api/app/main.py`, and `apps/api/app/api/routes/mcp_tasks.py`: register routes and remove unique-account wording.
- Modify `apps/api/app/core/config.py`: remove the static MCP hash setting.

### MCP gateway

- Modify `apps/mcp/app/api_client.py`: add the empty-body internal verification call.
- Modify `apps/mcp/app/middleware.py`: replace static digest comparison with API-backed verification.
- Modify `apps/mcp/app/main.py`: share the lifecycle API client with transport authentication.
- Modify `apps/mcp/app/auth.py`: retain strict Bearer parsing and remove static digest comparison.
- Modify `apps/mcp/app/config.py`: remove `token_sha256` and its production requirement.

### Web

- Create `apps/web/src/features/settings/settings-api.ts`: account and Token HTTP client.
- Create `apps/web/src/features/settings/settings-page.tsx`: settings composition.
- Create `apps/web/src/features/settings/password-settings.tsx`: password form.
- Create `apps/web/src/features/settings/mcp-token-settings.tsx`: token create/list/revoke UI.
- Create `apps/web/src/features/auth/app-navigation.ts`: two-route History API navigation.
- Create `apps/web/src/features/auth/authenticated-header.tsx`: shared authenticated header.
- Modify `apps/web/src/features/auth/auth-context.tsx`: password-change completion notice.
- Modify `apps/web/src/features/auth/login-form.tsx`: render the safe notice.
- Modify `apps/web/src/features/auth/authenticated-shell.tsx`: shared header and page selection.
- Modify `apps/web/src/features/tasks/todo-workspace.tsx`: remove the account header now owned by the shell.
- Modify `apps/web/src/index.css`: responsive settings and shared-shell styles.

### Deployment and docs

- Modify `compose.yaml`, `.env.example`, and `scripts/check-compose.ps1`: remove the shared static hash.
- Modify `docs/development.md`, `docs/mcp.md`, and `docs/mcp-client-deployment.md`: document Web-issued user tokens and rotation.

---

### Task 1: Add the MCP Token schema and authentication version migration

**Files:**
- Create: `apps/api/alembic/versions/0004_multi_user_mcp_tokens.py`
- Create: `apps/api/app/models/mcp_token.py`
- Modify: `apps/api/app/models/user.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/tests/test_models.py`
- Modify: `apps/api/tests/test_migrations.py`

- [ ] **Step 1: Write failing ORM and migration tests**

Extend `apps/api/tests/test_models.py` so the metadata contract requires the new table, user version and cascade:

First update the existing metadata table-name assertion to the complete set:

```python
assert set(Base.metadata.tables) == {"users", "auth_sessions", "tasks", "mcp_tokens"}
```

```python
from app.models import AuthSession, McpToken, Task, User


def test_models_expose_mcp_token_security_columns(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    inspector = inspect(engine)
    user_columns = {column["name"]: column for column in inspector.get_columns("users")}
    token_columns = {
        column["name"]: column for column in inspector.get_columns("mcp_tokens")
    }
    token_uniques = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspector.get_unique_constraints("mcp_tokens")
    }

    assert str(user_columns["auth_version"]["default"]).strip("'\"()") == "1"
    assert user_columns["auth_version"]["nullable"] is False
    assert {
        "id",
        "user_id",
        "name",
        "token_hash",
        "expires_at",
        "revoked_at",
        "last_used_at",
        "created_at",
    } == token_columns.keys()
    assert token_uniques["uq_mcp_tokens_token_hash"] == ["token_hash"]

    with session_factory() as session:
        user = User(username="person", password_hash="hash")
        user.mcp_tokens.append(
            McpToken(name="家中 Codex", token_hash="a" * 64)
        )
        session.add(user)
        session.commit()
        user_id = user.id
        session.delete(user)
        session.commit()
        assert session.query(McpToken).filter_by(user_id=user_id).count() == 0
    engine.dispose()
```

Add a focused upgrade/downgrade case to `apps/api/tests/test_migrations.py`:

```python
def test_multi_user_mcp_migration_preserves_users_and_downgrades_cleanly(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'multi-user-mcp.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0003_add_cancelled_task_status")
    engine = create_engine_for_settings(
        type("Settings", (), {"database_url": database_url})()
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, username, password_hash, timezone, is_active, next_task_serial, "
            "created_at, updated_at) VALUES "
            "('u1', 'owner', 'hash', 'Asia/Shanghai', 1, 1, "
            "'2026-08-25', '2026-08-25')"
        )

    command.upgrade(config, "head")
    upgraded = inspect(engine)
    assert "mcp_tokens" in upgraded.get_table_names()
    assert "auth_version" in {
        column["name"] for column in upgraded.get_columns("users")
    }
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT username, auth_version FROM users WHERE id='u1'"
        ).one() == ("owner", 1)

    command.downgrade(config, "0003_add_cancelled_task_status")
    downgraded = inspect(engine)
    assert "mcp_tokens" not in downgraded.get_table_names()
    assert "auth_version" not in {
        column["name"] for column in downgraded.get_columns("users")
    }
    engine.dispose()
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
mise exec -- pnpm test:api -- tests/test_models.py tests/test_migrations.py -q
```

Expected: FAIL because `McpToken`, `mcp_tokens`, `users.auth_version`, and revision `0004_multi_user_mcp_tokens` do not exist.

- [ ] **Step 3: Implement the ORM model and relationships**

Create `apps/api/app/models/mcp_token.py`:

```python
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
    """用户自行管理的长期 MCP 凭据元数据，永不持久化原始 secret。"""

    __tablename__ = "mcp_tokens"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 64", name="ck_mcp_tokens_name_length"),
        CheckConstraint(
            "length(token_hash) = 64 AND token_hash = lower(token_hash) "
            "AND token_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_mcp_tokens_hash_format",
        ),
        UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now)

    user: Mapped["User"] = relationship(back_populates="mcp_tokens")
```

Modify `User` with `auth_version` and the relationship, and export the model:

```python
auth_version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
mcp_tokens: Mapped[list["McpToken"]] = relationship(
    back_populates="user", cascade="all, delete-orphan"
)
```

Update the `TYPE_CHECKING` import in `user.py` and `__all__` in `models/__init__.py`.

- [ ] **Step 4: Add Alembic revision `0004_multi_user_mcp_tokens`**

Create `apps/api/alembic/versions/0004_multi_user_mcp_tokens.py` with `down_revision = "0003_add_cancelled_task_status"`. The upgrade must add `auth_version` before creating the child table:

```python
def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 64", name="ck_mcp_tokens_name_length"
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64 AND token_hash = lower(token_hash) "
            "AND token_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_mcp_tokens_hash_format",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_tokens_user_id", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
    with op.batch_alter_table("users", recreate="always") as batch_op:
        batch_op.drop_column("auth_version")
```

- [ ] **Step 5: Run the focused tests and confirm GREEN**

Run the Step 2 command again.

Expected: PASS for model and migration tests, including upgrade/downgrade preservation.

- [ ] **Step 6: Commit when explicitly authorized**

```powershell
git add -- apps/api/alembic/versions/0004_multi_user_mcp_tokens.py apps/api/app/models/mcp_token.py apps/api/app/models/user.py apps/api/app/models/__init__.py apps/api/tests/test_models.py apps/api/tests/test_migrations.py
git commit -m "feat(api): 增加用户级MCP Token数据模型"
```

### Task 2: Allow multiple CLI accounts and version Web access tokens

**Files:**
- Modify: `apps/api/app/services/accounts.py`
- Modify: `apps/api/app/cli.py`
- Modify: `apps/api/app/core/security.py`
- Modify: `apps/api/app/services/auth.py`
- Modify: `apps/api/tests/test_accounts.py`
- Modify: `apps/api/tests/test_cli.py`
- Modify: `apps/api/tests/test_security.py`
- Modify: `apps/api/tests/test_auth_service.py`

- [ ] **Step 1: Replace the single-account tests with multi-account and auth-version tests**

Update the CLI test to prove different usernames succeed while duplicates still fail:

```python
def test_create_cli_allows_multiple_accounts_but_rejects_duplicate_username(
    monkeypatch: pytest.MonkeyPatch,
    cli_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_password_answers(
        monkeypatch,
        PASSWORD,
        PASSWORD,
        NEW_PASSWORD,
        NEW_PASSWORD,
        PASSWORD,
        PASSWORD,
    )
    assert main(["user", "create", "--username", "potato"]) == 0
    assert main(["user", "create", "--username", "second"]) == 0
    assert main(["user", "create", "--username", "Potato"]) != 0
    captured = capsys.readouterr()
    assert "用户名已存在" in captured.err
    assert PASSWORD not in captured.out + captured.err
    assert cli_database_url not in captured.out + captured.err
```

Update the account service test:

```python
def test_create_account_allows_multiple_unique_usernames(session: Session) -> None:
    first = create_account(session, " Potato ", PASSWORD)
    second = create_account(session, "second", NEW_PASSWORD)
    assert {first.username, second.username} == {"potato", "second"}
    with pytest.raises(AccountAlreadyExists):
        create_account(session, "POTATO", PASSWORD)
```

Add security/service assertions that access tokens require and enforce `ver`:

```python
def test_access_token_requires_auth_version(settings: Settings) -> None:
    token = issue_access_token("user-id", 3, settings)
    payload = decode_token(token, "access", settings)
    assert payload.ver == 3


def test_access_token_authentication_rejects_old_auth_version(
    session: Session, settings: Settings
) -> None:
    user = create_account(session, "potato", PASSWORD)
    token = issue_access_token(user.id, user.auth_version, settings)
    user.auth_version += 1
    session.commit()
    with pytest.raises(AuthenticationRequired):
        authenticate_access_token(session, token, settings)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

```powershell
mise exec -- pnpm test:api -- tests/test_accounts.py tests/test_cli.py tests/test_security.py tests/test_auth_service.py -q
```

Expected: FAIL because the service still refuses a second account and access tokens have no `ver` claim.

- [ ] **Step 3: Change account creation to username uniqueness**

In `accounts.py`, remove the global account count and map only the database uniqueness conflict to `AccountAlreadyExists`:

```python
def create_account(session: Session, username: str, password: str) -> User:
    """创建用户名唯一的账号；数据库唯一约束是并发写入的最终保护。"""
    normalized = normalize_username(username)
    user = User(username=normalized, password_hash=hash_password(password))
    try:
        session.add(user)
        session.commit()
        return user
    except IntegrityError as error:
        session.rollback()
        raise AccountAlreadyExists from error
    except Exception:
        session.rollback()
        raise
```

Remove unused `func`/`text` imports. Update CLI wording to `维护账号` and map `AccountAlreadyExists` to `错误：用户名已存在`.

- [ ] **Step 4: Add the access-token version claim**

In `core/security.py`, import `Field` from Pydantic, add an optional but strict positive parsed field, and require it for access tokens so pre-migration access tokens fail closed while old refresh tokens can still be rotated:

```python
class TokenPayload(BaseModel):
    sub: str
    jti: str
    type: Literal["access", "refresh"]
    iss: str
    aud: str | list[str]
    iat: datetime
    exp: datetime
    sid: str | None = None
    ver: int | None = Field(default=None, strict=True, ge=1)


def issue_access_token(user_id: str, auth_version: int, settings: Settings) -> str:
    now = datetime.now(UTC)
    return _encode_token(
        {
            "sub": user_id,
            "jti": str(uuid4()),
            "type": "access",
            "ver": auth_version,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
        },
        settings,
    )
```

After Pydantic validation in `decode_token`, reject access payloads whose `ver` is missing. Pydantic rejects booleans, strings, zero and negative versions before this branch.

Update `login_user` and `refresh_session` to call `issue_access_token(user.id, user.auth_version, settings)`. Update `authenticate_access_token`:

```python
user = session.get(User, payload.sub)
if (
    user is None
    or not user.is_active
    or payload.ver is None
    or payload.ver != user.auth_version
):
    raise AuthenticationRequired
return user
```

- [ ] **Step 5: Centralize password rotation for later CLI and Web reuse**

Add a transaction-local helper in `accounts.py` and make the CLI reset call it:

```python
def _apply_password_change(user: User, password_hash: str) -> None:
    """修改密码并递增凭据版本；调用方负责在同一事务撤销会话。"""
    user.password_hash = password_hash
    user.auth_version += 1


def change_password(session: Session, username: str, password: str) -> User:
    try:
        user = _find_account(session, normalize_username(username))
        now = utc_now()
        _apply_password_change(user, hash_password(password))
        _revoke_active_sessions(session, user.id, now)
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise
```

Extend `test_change_password_revokes_all_active_sessions_in_one_committed_state` to assert `user.auth_version == 2`.

- [ ] **Step 6: Run the focused tests and confirm GREEN**

Run the Step 2 command again.

Expected: PASS; two distinct CLI users are created, duplicates are rejected safely, and old access-token versions fail authentication.

- [ ] **Step 7: Commit when explicitly authorized**

```powershell
git add -- apps/api/app/services/accounts.py apps/api/app/cli.py apps/api/app/core/security.py apps/api/app/services/auth.py apps/api/tests/test_accounts.py apps/api/tests/test_cli.py apps/api/tests/test_security.py apps/api/tests/test_auth_service.py
git commit -m "feat(auth): 支持多账号与会话版本"
```

### Task 3: Add self-service password change

**Files:**
- Create: `apps/api/app/schemas/account.py`
- Create: `apps/api/app/api/auth_cookies.py`
- Create: `apps/api/app/api/routes/account.py`
- Modify: `apps/api/app/api/routes/auth.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/services/accounts.py`
- Create: `apps/api/tests/test_account_api.py`
- Modify: `apps/api/tests/test_accounts.py`
- Modify: `apps/api/tests/test_auth_api.py`

- [ ] **Step 1: Write failing account-service and HTTP tests**

Create `apps/api/tests/test_account_api.py` with the same migrated temporary-database pattern as `test_auth_api.py`. Cover wrong current password, unchanged password, success, cookie deletion and immediate invalidation:

```python
def test_password_change_revokes_sessions_and_invalidates_old_access_token(
    account_client: TestClient,
) -> None:
    login_response = login(account_client)
    old_access = login_response.json()["access_token"]

    changed = account_client.put(
        "/api/v1/account/password",
        headers={"Authorization": f"Bearer {old_access}"},
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
        },
    )

    assert changed.status_code == 204
    assert changed.content == b""
    assert "Max-Age=0" in changed.headers["set-cookie"]
    rejected = account_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {old_access}"},
    )
    assert rejected.status_code == 401
    assert account_client.post("/api/v1/auth/refresh").status_code == 401
    assert login(account_client, password=NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("current_password", "new_password", "code"),
    [
        ("wrong password", NEW_PASSWORD, "invalid_current_password"),
        (PASSWORD, PASSWORD, "password_unchanged"),
    ],
)
def test_password_change_returns_stable_safe_errors(
    account_client: TestClient,
    current_password: str,
    new_password: str,
    code: str,
) -> None:
    access = login(account_client).json()["access_token"]
    response = account_client.put(
        "/api/v1/account/password",
        headers={"Authorization": f"Bearer {access}"},
        json={"current_password": current_password, "new_password": new_password},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert current_password not in response.text
    assert new_password not in response.text


def test_password_change_does_not_log_passwords_or_access_token(
    account_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    access = login(account_client).json()["access_token"]
    caplog.clear()

    with caplog.at_level(logging.INFO):
        response = account_client.put(
            "/api/v1/account/password",
            headers={"Authorization": f"Bearer {access}"},
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )

    assert response.status_code == 204
    assert PASSWORD not in caplog.text
    assert NEW_PASSWORD not in caplog.text
    assert access not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {"current_password": PASSWORD, "new_password": "short"},
        {
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "user_id": "other-user",
        },
    ],
)
def test_password_change_reuses_password_policy_and_rejects_identity_fields(
    account_client: TestClient,
    payload: dict[str, str],
) -> None:
    access = login(account_client).json()["access_token"]
    response = account_client.put(
        "/api/v1/account/password",
        headers={"Authorization": f"Bearer {access}"},
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert payload["new_password"] not in response.text
```

Add the required `import logging` beside the existing test imports. The assertions cover both response safety and request-logging safety; never log the request model or Authorization header to satisfy them.

Add a service rollback test to `test_accounts.py` that monkeypatches session commit to fail and asserts the password hash, `auth_version`, and active sessions remain unchanged after rollback.

- [ ] **Step 2: Run tests and confirm RED**

```powershell
mise exec -- pnpm test:api -- tests/test_account_api.py tests/test_accounts.py tests/test_auth_api.py -q
```

Expected: FAIL because the account route, schema, current-password exceptions and shared cookie helpers do not exist.

- [ ] **Step 3: Add the self-service domain operation**

In `accounts.py`, add stable exceptions and a current-user operation:

```python
class InvalidCurrentPassword(Exception):
    """当前密码不匹配。"""


class PasswordUnchanged(Exception):
    """新密码与当前密码相同。"""


def change_own_password(
    session: Session,
    user: User,
    current_password: str,
    new_password: str,
) -> None:
    try:
        if not verify_password(current_password, user.password_hash):
            raise InvalidCurrentPassword
        if current_password == new_password:
            raise PasswordUnchanged
        now = utc_now()
        _apply_password_change(user, hash_password(new_password))
        _revoke_active_sessions(session, user.id, now)
        session.commit()
    except Exception:
        session.rollback()
        raise
```

- [ ] **Step 4: Extract cookie helpers and implement the route**

Move `_set_refresh_cookie`, `_delete_refresh_cookie`, and `_refresh_cookie_path` without semantic changes from `routes/auth.py` into `api/auth_cookies.py`; import them back into the auth routes.

Create `schemas/account.py`:

```python
from pydantic import BaseModel, ConfigDict, field_validator

from app.core.security import validate_password


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def enforce_existing_password_policy(cls, value: str) -> str:
        return validate_password(value)
```

Create `routes/account.py`:

```python
from fastapi import APIRouter, Request, Response, status

from app.api.auth_cookies import delete_refresh_cookie
from app.api.dependencies import CurrentUser, DbSession
from app.core.errors import AppError
from app.schemas.account import PasswordChangeRequest
from app.services.accounts import (
    InvalidCurrentPassword,
    PasswordUnchanged,
    change_own_password,
)

router = APIRouter(prefix="/account", tags=["account"])


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    session: DbSession,
    user: CurrentUser,
) -> None:
    try:
        change_own_password(session, user, payload.current_password, payload.new_password)
    except InvalidCurrentPassword as error:
        raise AppError(
            status_code=400,
            code="invalid_current_password",
            message="当前密码错误",
        ) from error
    except PasswordUnchanged as error:
        raise AppError(
            status_code=400,
            code="password_unchanged",
            message="新密码不能与当前密码相同",
        ) from error
    delete_refresh_cookie(response, request.app.state.settings)
```

Register `account_router` in `api/router.py`. Ensure validation errors still use the repository-wide `validation_error` envelope.

- [ ] **Step 5: Run tests and confirm GREEN**

Run the Step 2 command again.

Expected: PASS; the old access token and refresh session stop working immediately, and no password appears in responses.

- [ ] **Step 6: Commit when explicitly authorized**

```powershell
git add -- apps/api/app/schemas/account.py apps/api/app/api/auth_cookies.py apps/api/app/api/routes/account.py apps/api/app/api/routes/auth.py apps/api/app/api/router.py apps/api/app/services/accounts.py apps/api/tests/test_account_api.py apps/api/tests/test_accounts.py apps/api/tests/test_auth_api.py
git commit -m "feat(account): 增加用户密码修改接口"
```

### Task 4: Implement user-level MCP Token lifecycle services

**Files:**
- Create: `apps/api/app/services/mcp_tokens.py`
- Create: `apps/api/tests/test_mcp_tokens.py`

- [ ] **Step 1: Write failing service tests for creation, isolation, expiry and revocation**

Create a migrated temporary database fixture in `test_mcp_tokens.py`, create two users, and cover the full domain boundary:

```python
def test_create_token_returns_secret_once_and_persists_only_digest(
    session: Session,
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "家中 Codex", 365)

    assert issued.raw_token.startswith(f"tickly_mcp_{issued.record.id}.")
    assert issued.record.token_hash == hashlib.sha256(
        issued.raw_token.encode("utf-8")
    ).hexdigest()
    assert issued.raw_token not in issued.record.token_hash
    assert issued.record.user_id == user.id
    assert issued.record.expires_at is not None


def test_user_can_create_multiple_tokens_and_reuse_display_name(
    session: Session,
) -> None:
    user = create_account(session, "first", PASSWORD)
    first = create_mcp_token(session, user.id, "Codex", 365)
    second = create_mcp_token(session, user.id, "Codex", None)

    assert first.record.id != second.record.id
    assert first.raw_token != second.raw_token
    assert [item.id for item in list_mcp_tokens(session, user.id)] == [
        second.record.id,
        first.record.id,
    ]


def test_authenticate_token_resolves_only_its_active_owner(session: Session) -> None:
    first = create_account(session, "first", PASSWORD)
    second = create_account(session, "second", PASSWORD)
    issued = create_mcp_token(session, second.id, "公司 Codex", 90)

    authenticated = authenticate_mcp_token(session, issued.raw_token)

    assert authenticated.id == second.id
    assert authenticated.id != first.id


@pytest.mark.parametrize("state", ["expired", "revoked", "inactive"])
def test_authenticate_token_fails_closed_for_terminal_states(
    session: Session, state: str
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "测试", None)
    if state == "expired":
        issued.record.expires_at = utc_now() - timedelta(seconds=1)
    elif state == "revoked":
        issued.record.revoked_at = utc_now()
    else:
        user.is_active = False
    session.commit()

    with pytest.raises(McpAuthenticationRequired):
        authenticate_mcp_token(session, issued.raw_token)


def test_revoke_token_is_idempotent_and_scoped_to_owner(session: Session) -> None:
    first = create_account(session, "first", PASSWORD)
    second = create_account(session, "second", PASSWORD)
    issued = create_mcp_token(session, first.id, "测试", None)

    with pytest.raises(McpTokenNotFound):
        revoke_mcp_token(session, second.id, issued.record.id)
    revoke_mcp_token(session, first.id, issued.record.id)
    first_revoked_at = issued.record.revoked_at
    revoke_mcp_token(session, first.id, issued.record.id)
    assert issued.record.revoked_at == first_revoked_at
```

Add a timestamp-throttling test: authenticate once, capture `last_used_at`, authenticate again less than one hour later, and assert the stored value did not change. Also prove a telemetry write failure is not converted into a credential failure:

```python
def test_last_used_write_failure_surfaces_as_database_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_account(session, "first", PASSWORD)
    issued = create_mcp_token(session, user.id, "遥测失败", None)

    def fail_commit() -> None:
        raise RuntimeError("数据库遥测写入失败")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="数据库遥测写入失败"):
        authenticate_mcp_token(session, issued.raw_token)
```

This test intentionally expects the database failure itself: only invalid, expired, revoked or inactive credentials map to `McpAuthenticationRequired`.

- [ ] **Step 2: Run the service tests and confirm RED**

```powershell
mise exec -- pnpm test:api -- tests/test_mcp_tokens.py -q
```

Expected: FAIL because `services.mcp_tokens` and its domain types do not exist.

- [ ] **Step 3: Implement Token generation and storage**

Create `services/mcp_tokens.py` with explicit domain exceptions and return types:

```python
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import secrets
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpToken, User
from app.models.user import utc_now

TOKEN_PREFIX = "tickly_mcp_"
_LAST_USED_INTERVAL = timedelta(hours=1)
_DUMMY_DIGEST = "0" * 64


class McpAuthenticationRequired(Exception):
    """MCP Token 无法解析为当前活跃用户。"""


class McpTokenNotFound(Exception):
    """当前用户不存在指定 Token。"""


@dataclass(frozen=True)
class IssuedMcpToken:
    record: McpToken
    raw_token: str


def create_mcp_token(
    session: Session,
    user_id: str,
    name: str,
    expires_in_days: int | None,
) -> IssuedMcpToken:
    now = utc_now()
    token_id = str(uuid4())
    raw_token = f"{TOKEN_PREFIX}{token_id}.{secrets.token_urlsafe(32)}"
    record = McpToken(
        id=token_id,
        user_id=user_id,
        name=name.strip(),
        token_hash=_digest(raw_token),
        expires_at=(
            now + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        ),
        created_at=now,
    )
    try:
        session.add(record)
        session.commit()
        return IssuedMcpToken(record=record, raw_token=raw_token)
    except Exception:
        session.rollback()
        raise


def list_mcp_tokens(session: Session, user_id: str) -> list[McpToken]:
    return list(
        session.scalars(
            select(McpToken)
            .where(McpToken.user_id == user_id)
            .order_by(McpToken.created_at.desc(), McpToken.id.desc())
        ).all()
    )
```

- [ ] **Step 4: Implement parsing, authentication, status and revocation**

Continue in the same file:

```python
def authenticate_mcp_token(session: Session, raw_token: str) -> User:
    token_id = _token_id(raw_token)
    record = session.get(McpToken, token_id) if token_id is not None else None
    actual = _digest(raw_token)
    expected = record.token_hash if record is not None else _DUMMY_DIGEST
    digest_matches = hmac.compare_digest(actual, expected)
    now = utc_now()
    if (
        record is None
        or not digest_matches
        or record.revoked_at is not None
        or _is_expired(record, now)
    ):
        raise McpAuthenticationRequired
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise McpAuthenticationRequired

    last_used = _as_utc(record.last_used_at)
    if last_used is None or last_used <= now - _LAST_USED_INTERVAL:
        record.last_used_at = now
        try:
            session.commit()
        except Exception:
            # 数据库写入失败属于上游不可用，不能伪装成凭据错误。
            session.rollback()
            raise
    return user


def revoke_mcp_token(session: Session, user_id: str, token_id: str) -> None:
    record = session.scalar(
        select(McpToken).where(
            McpToken.id == token_id,
            McpToken.user_id == user_id,
        )
    )
    if record is None:
        raise McpTokenNotFound
    if record.revoked_at is None:
        record.revoked_at = utc_now()
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise


def mcp_token_status(record: McpToken, now: datetime | None = None) -> str:
    current = now or utc_now()
    if record.revoked_at is not None:
        return "revoked"
    if _is_expired(record, current):
        return "expired"
    return "active"


def _token_id(raw_token: str) -> str | None:
    locator, separator, secret = raw_token.partition(".")
    if separator != "." or not secret or not locator.startswith(TOKEN_PREFIX):
        return None
    candidate = locator.removeprefix(TOKEN_PREFIX)
    try:
        parsed = UUID(candidate)
    except ValueError:
        return None
    return candidate if str(parsed) == candidate else None


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_expired(record: McpToken, now: datetime) -> bool:
    expires_at = _as_utc(record.expires_at)
    return expires_at is not None and expires_at <= now
```

- [ ] **Step 5: Run the service tests and confirm GREEN**

Run the Step 2 command again.

Expected: PASS for multi-user resolution, one-time secret storage, expiry, revocation and one-hour `last_used_at` throttling.

- [ ] **Step 6: Commit when explicitly authorized**

```powershell
git add -- apps/api/app/services/mcp_tokens.py apps/api/tests/test_mcp_tokens.py
git commit -m "feat(mcp): 增加用户Token生命周期服务"
```

### Task 5: Expose Web MCP Token management APIs

**Files:**
- Create: `apps/api/app/schemas/mcp_tokens.py`
- Create: `apps/api/app/api/routes/mcp_tokens.py`
- Modify: `apps/api/app/api/router.py`
- Create: `apps/api/tests/test_mcp_tokens_api.py`

- [ ] **Step 1: Write failing HTTP contract and ownership tests**

Create `test_mcp_tokens_api.py` with two CLI-created users and separate Web logins. Cover response filtering, default 365 days, all three expiry choices, one-time raw secret and ownership:

```python
def test_user_can_create_list_and_revoke_only_owned_tokens(
    token_client: TestClient,
) -> None:
    first_access = login(token_client, "first", PASSWORD)
    created = token_client.post(
        "/api/v1/mcp-tokens",
        headers=bearer(first_access),
        json={"name": "家中 Codex", "expires_in_days": 365},
    )
    assert created.status_code == 201
    body = created.json()
    raw_token = body["token"]
    token_id = body["item"]["id"]
    assert raw_token.startswith(f"tickly_mcp_{token_id}.")
    assert created.headers["cache-control"] == "no-store"

    listed = token_client.get("/api/v1/mcp-tokens", headers=bearer(first_access))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == token_id
    for field in ("created_at", "expires_at"):
        value = datetime.fromisoformat(
            listed.json()["items"][0][field].replace("Z", "+00:00")
        )
        assert value.utcoffset() == timedelta(0)
    assert raw_token not in listed.text
    assert "token_hash" not in listed.text

    second_access = login(token_client, "second", PASSWORD)
    hidden = token_client.delete(
        f"/api/v1/mcp-tokens/{token_id}", headers=bearer(second_access)
    )
    assert hidden.status_code == 404
    revoked = token_client.delete(
        f"/api/v1/mcp-tokens/{token_id}", headers=bearer(first_access)
    )
    repeated = token_client.delete(
        f"/api/v1/mcp-tokens/{token_id}", headers=bearer(first_access)
    )
    assert revoked.status_code == repeated.status_code == 204


def test_token_creation_does_not_log_raw_token_or_digest(
    token_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    access = login(token_client, "first", PASSWORD)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        response = token_client.post(
            "/api/v1/mcp-tokens",
            headers=bearer(access),
            json={"name": "日志安全", "expires_in_days": 365},
        )

    raw_token = response.json()["token"]
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    assert response.status_code == 201
    assert raw_token not in caplog.text
    assert digest not in caplog.text
    assert access not in caplog.text
```

Add `import hashlib`, `import logging`, and `from datetime import datetime, timedelta` beside the existing test imports. The create response is the only allowed raw-Token exposure; logs, list responses and error envelopes must contain neither the raw value nor its digest.

Add parameterized schema tests for `90`, `365`, `None`, and rejection of `0`, `30`, `366`, strings and booleans. Assert an extra `user_id` field returns the same `422 validation_error`, proving ownership can only come from `CurrentUser`. Add a test that `GET` order is newest first and status projection prioritizes `revoked` over `expired`.

- [ ] **Step 2: Run the API tests and confirm RED**

```powershell
mise exec -- pnpm test:api -- tests/test_mcp_tokens_api.py -q
```

Expected: FAIL because schemas and `/api/v1/mcp-tokens` routes do not exist.

- [ ] **Step 3: Implement strict request and response schemas**

Create `schemas/mcp_tokens.py`:

```python
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator

McpTokenStatus = Literal["active", "expired", "revoked"]
McpTokenExpiryDays = Annotated[Literal[90, 365], Strict()]


class McpTokenCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    expires_in_days: McpTokenExpiryDays | None = 365

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Token 名称不能为空")
        return normalized


class McpTokenResponse(BaseModel):
    id: str
    name: str
    status: McpTokenStatus
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class McpTokenListResponse(BaseModel):
    items: list[McpTokenResponse]


class McpTokenCreateResponse(BaseModel):
    item: McpTokenResponse
    token: str
```

- [ ] **Step 4: Implement current-user-only routes**

Create `routes/mcp_tokens.py` with a private response projection:

```python
from fastapi import APIRouter, Response, status

from app.api.dependencies import CurrentUser, DbSession
from app.core.errors import AppError
from app.models import McpToken
from app.schemas.mcp_tokens import (
    McpTokenCreateRequest,
    McpTokenCreateResponse,
    McpTokenListResponse,
    McpTokenResponse,
)
from app.services.mcp_tokens import (
    McpTokenNotFound,
    create_mcp_token,
    list_mcp_tokens,
    mcp_token_status,
    revoke_mcp_token,
)

router = APIRouter(prefix="/mcp-tokens", tags=["mcp-tokens"])


@router.get("", response_model=McpTokenListResponse)
def list_all(session: DbSession, user: CurrentUser) -> McpTokenListResponse:
    return McpTokenListResponse(
        items=[_response(item) for item in list_mcp_tokens(session, user.id)]
    )


@router.post("", response_model=McpTokenCreateResponse, status_code=201)
def create(
    payload: McpTokenCreateRequest,
    response: Response,
    session: DbSession,
    user: CurrentUser,
) -> McpTokenCreateResponse:
    issued = create_mcp_token(
        session, user.id, payload.name, payload.expires_in_days
    )
    response.headers["Cache-Control"] = "no-store"
    return McpTokenCreateResponse(
        item=_response(issued.record), token=issued.raw_token
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(token_id: str, session: DbSession, user: CurrentUser) -> None:
    try:
        revoke_mcp_token(session, user.id, token_id)
    except McpTokenNotFound as error:
        raise AppError(
            status_code=404, code="mcp_token_not_found", message="MCP Token 不存在"
        ) from error


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _response(record: McpToken) -> McpTokenResponse:
    return McpTokenResponse(
        id=record.id,
        name=record.name,
        status=mcp_token_status(record),
        expires_at=_utc(record.expires_at),
        last_used_at=_utc(record.last_used_at),
        created_at=_utc(record.created_at),
    )
```

Register the router in `api/router.py`.

- [ ] **Step 5: Run the API tests and confirm GREEN**

Run the Step 2 command again.

Expected: PASS; raw tokens only appear in the create response and cross-user IDs return the same 404 as missing IDs.

- [ ] **Step 6: Commit when explicitly authorized**

```powershell
git add -- apps/api/app/schemas/mcp_tokens.py apps/api/app/api/routes/mcp_tokens.py apps/api/app/api/router.py apps/api/tests/test_mcp_tokens_api.py
git commit -m "feat(mcp): 增加用户Token管理接口"
```

### Task 6: Replace API unique-account MCP authentication with token ownership

**Files:**
- Create: `apps/api/app/api/routes/mcp_auth.py`
- Modify: `apps/api/app/api/mcp_dependencies.py`
- Modify: `apps/api/app/api/routes/mcp_tasks.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/tests/test_mcp_dependencies.py`
- Modify: `apps/api/tests/test_mcp_tasks_api.py`
- Modify: `apps/api/tests/test_config.py`

- [ ] **Step 1: Rewrite failing dependency and real HTTP isolation tests**

Replace the static digest/unique-account cases in `test_mcp_dependencies.py` with database-token cases:

```python
def test_get_mcp_current_user_resolves_token_owner(session: Session) -> None:
    first = create_account(session, "first", PASSWORD)
    second = create_account(session, "second", PASSWORD)
    issued = create_mcp_token(session, second.id, "第二账号", None)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=issued.raw_token
    )
    resolved = get_mcp_current_user(session, credentials)
    assert resolved.id == second.id
    assert resolved.id != first.id


@pytest.mark.parametrize("token", ["wrong", "tickly_mcp_invalid.secret"])
def test_get_mcp_current_user_hides_all_token_failures(
    session: Session, token: str
) -> None:
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as raised:
        get_mcp_current_user(session, credentials)
    assert raised.value.status_code == 401
    assert raised.value.code == "authentication_required"
    assert token not in str(raised.value)
```

Add `test_cli_created_users_with_same_serial_are_isolated_over_http` to `test_mcp_tasks_api.py`. Invoke the real CLI `main(["user", "create", ...])` twice with monkeypatched password prompts against the fixture database, log both users in over HTTP, create each user’s Token through `POST /api/v1/mcp-tokens`, and give both a public task whose account-local `serial` is `1`. Assert each `/internal/mcp/v1/tasks/1` response contains only its owner’s title. Also assert a Web JWT is rejected by the internal route and an MCP token is rejected by `/api/v1/tasks`.

Cover the revoke-between-check-and-use race with two separate HTTP requests:

```python
def test_task_route_revalidates_after_transport_verification(
    mcp_client: TestClient,
    session: Session,
) -> None:
    user = create_account(session, "race-user", PASSWORD)
    create_task(session, user.id, task_payload("只属于当前用户"))
    issued = create_mcp_token(session, user.id, "竞态测试", None)
    headers = bearer(issued.raw_token)

    verified = mcp_client.post("/internal/mcp/v1/auth/verify", headers=headers)
    assert verified.status_code == 204

    revoke_mcp_token(session, user.id, issued.record.id)
    blocked = mcp_client.get("/internal/mcp/v1/tasks/1", headers=headers)
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"
```

Add an OpenAPI boundary test after registering all new routers:

```python
def test_settings_routes_are_public_but_internal_mcp_routes_stay_hidden(
    mcp_client: TestClient,
) -> None:
    paths = mcp_client.get("/openapi.json").json()["paths"]
    assert "/api/v1/account/password" in paths
    assert "/api/v1/mcp-tokens" in paths
    assert "/internal/mcp/v1/auth/verify" not in paths
    assert "/internal/mcp/v1/tasks/{serial}" not in paths
```

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
mise exec -- pnpm test:api -- tests/test_mcp_dependencies.py tests/test_mcp_tasks_api.py tests/test_config.py -q
```

Expected: FAIL because the dependency still reads the static setting and rejects databases with multiple users.

- [ ] **Step 3: Replace the dependency and add the internal verify route**

Rewrite `mcp_dependencies.py` to delegate to the database service:

```python
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import DbSession
from app.core.errors import AppError
from app.models import User
from app.services.mcp_tokens import (
    McpAuthenticationRequired,
    authenticate_mcp_token,
)

_bearer = HTTPBearer(auto_error=False)
McpBearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None, Depends(_bearer)
]


def get_mcp_current_user(
    session: DbSession,
    credentials: McpBearerCredentials,
) -> User:
    if credentials is None:
        raise _authentication_required()
    try:
        return authenticate_mcp_token(session, credentials.credentials)
    except McpAuthenticationRequired as error:
        raise _authentication_required() from error


def _authentication_required() -> AppError:
    return AppError(
        status_code=401,
        code="authentication_required",
        message="需要 MCP 认证",
        headers={"WWW-Authenticate": "Bearer"},
    )


McpCurrentUser = Annotated[User, Depends(get_mcp_current_user)]
```

Create `routes/mcp_auth.py`:

```python
from fastapi import APIRouter, status

from app.api.mcp_dependencies import McpCurrentUser

router = APIRouter(prefix="/internal/mcp/v1/auth", include_in_schema=False)


@router.post("/verify", status_code=status.HTTP_204_NO_CONTENT)
def verify(user: McpCurrentUser) -> None:
    # 传输层只需要认证结论，用户身份继续由每个资源接口独立解析。
    del user
```

Register this internal router in `main.py`. Remove unique-account wording from `mcp_tasks.py` without changing its task service calls.

- [ ] **Step 4: Remove API static MCP configuration**

Delete `mcp_token_sha256` and its validator from `core/config.py`; update `test_config.py` to assert production settings no longer require or expose the field.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run the Step 2 command again.

Expected: PASS, including same-serial cross-user isolation and credential-domain separation.

- [ ] **Step 6: Commit when explicitly authorized**

```powershell
git add -- apps/api/app/api/routes/mcp_auth.py apps/api/app/api/mcp_dependencies.py apps/api/app/api/routes/mcp_tasks.py apps/api/app/main.py apps/api/app/core/config.py apps/api/tests/test_mcp_dependencies.py apps/api/tests/test_mcp_tasks_api.py apps/api/tests/test_config.py
git commit -m "feat(mcp): 按用户Token隔离内部任务"
```

### Task 7: Make the MCP gateway validate Bearer Tokens through the API

**Files:**
- Modify: `apps/mcp/app/auth.py`
- Modify: `apps/mcp/app/api_client.py`
- Modify: `apps/mcp/app/middleware.py`
- Modify: `apps/mcp/app/main.py`
- Modify: `apps/mcp/app/config.py`
- Modify: `apps/mcp/tests/test_auth.py`
- Modify: `apps/mcp/tests/test_api_client.py`
- Modify: `apps/mcp/tests/test_http_app.py`
- Modify: `apps/mcp/tests/test_config.py`
- Modify: `apps/mcp/tests/test_tools.py`

- [ ] **Step 1: Write failing upstream-verification client tests**

Add to `test_api_client.py`:

```python
@pytest.mark.asyncio
async def test_verify_token_requires_exact_204_and_forwards_security_context() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    client = make_client(handler)
    await client.verify_token(token=TOKEN, request_id=REQUEST_ID)

    assert requests[0].method == "POST"
    assert requests[0].url.path == "/internal/mcp/v1/auth/verify"
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert requests[0].headers["X-Request-ID"] == REQUEST_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "code"),
    [
        (401, {"error": {"code": "authentication_required"}}, "authentication_required"),
        (503, {"error": {"code": "database_busy"}}, "upstream_unavailable"),
        (200, {}, "upstream_contract_error"),
    ],
)
async def test_verify_token_maps_failures_without_echoing_token(
    status_code: int, body: dict[str, object], code: str
) -> None:
    client = make_client(lambda _: httpx.Response(status_code, json=body))
    with pytest.raises(McpToolError) as raised:
        await client.verify_token(token=TOKEN, request_id=REQUEST_ID)
    assert raised.value.code == code
    assert TOKEN not in str(raised.value)


@pytest.mark.asyncio
async def test_concurrent_verifications_keep_user_tokens_separate() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (request.headers["Authorization"], request.headers["X-Request-ID"])
        )
        return httpx.Response(204)

    client = make_client(handler)
    await asyncio.gather(
        client.verify_token(token="tickly_mcp_first.secret", request_id="req-first"),
        client.verify_token(token="tickly_mcp_second.secret", request_id="req-second"),
    )

    assert set(requests) == {
        ("Bearer tickly_mcp_first.secret", "req-first"),
        ("Bearer tickly_mcp_second.secret", "req-second"),
    }
```

Add `import asyncio` beside the existing test imports. This guards against storing a Token on the shared client or lifecycle object instead of passing it per request.

- [ ] **Step 2: Write failing transport middleware tests**

Change `test_http_app.py` so `make_settings()` no longer sets `token_sha256`. Install an upstream handler that returns `204` only for the valid test Token. Assert missing/Basic/invalid Bearer receive `401` before protocol parsing, valid Bearer reaches the protocol layer, and upstream failure receives fixed `503` without internal details.

```python
def test_mcp_transport_validates_each_bearer_through_api(monkeypatch: Any) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == f"Bearer {RAW_TOKEN}":
            return httpx.Response(204)
        return httpx.Response(
            401, json={"error": {"code": "authentication_required"}}
        )

    install_upstream(monkeypatch, handler)
    with TestClient(create_http_app(make_settings())) as client:
        invalid = client.post(
            "/mcp", headers={"Authorization": "Bearer wrong"}, json={}
        )
        valid = client.post("/mcp", headers=AUTH_HEADERS, json={})
    assert invalid.status_code == 401
    assert valid.status_code != 401
    assert [request.url.path for request in requests] == [
        "/internal/mcp/v1/auth/verify",
        "/internal/mcp/v1/auth/verify",
    ]
```

- [ ] **Step 3: Run MCP tests and confirm RED**

```powershell
mise exec -- pnpm test:mcp -- tests/test_auth.py tests/test_api_client.py tests/test_http_app.py tests/test_config.py tests/test_tools.py -q
```

Expected: FAIL because the gateway still compares a configured static digest and has no API verification method.

- [ ] **Step 4: Refactor the API client for empty verification responses**

Refactor the existing HTTP send logic so a private method returns a bounded, header-free `httpx.Response`. Keep `_request()` for JSON business endpoints and add:

```python
async def verify_token(self, *, token: str, request_id: str) -> None:
    response = await self._send(
        "POST",
        "/internal/mcp/v1/auth/verify",
        token=token,
        request_id=request_id,
    )
    if response.status_code == 204 and response.content == b"":
        return
    if response.status_code == 401:
        self._raise_known_error(response)
    if response.status_code >= 500:
        raise McpToolError(*_UNAVAILABLE_ERROR)
    raise McpToolError(*_CONTRACT_ERROR)
```

`_send()` must preserve the existing one-request/no-retry rule, response-size cap and exception sanitization. `_raise_known_error()` may only expose codes in `_KNOWN_MESSAGES`; remove obsolete `mcp_account_unavailable` from that map.

- [ ] **Step 5: Replace `StaticBearerMiddleware` with API-backed verification**

Keep `token_from_authorization` in `auth.py` and remove `bearer_matches`. In `middleware.py` define:

```python
from collections.abc import Awaitable, Callable

TokenVerifier = Callable[[str, str], Awaitable[None]]


class ApiBearerMiddleware:
    """在协议解析前把 Bearer 交给 API 权威验证，且不缓存认证结果。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: TokenVerifier,
        request_id_header: str,
    ) -> None:
        self.app = app
        self.verifier = verifier
        self.request_id_header = request_id_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        token = token_from_authorization(headers.get("Authorization"))
        request_id = headers.get(self.request_id_header)
        if token is None or request_id is None:
            await _auth_response(scope, receive, send)
            return
        try:
            await self.verifier(token, request_id)
        except McpToolError as error:
            if error.code == "authentication_required":
                await _auth_response(scope, receive, send)
            else:
                await JSONResponse(
                    {"error": "upstream_unavailable"}, status_code=503
                )(scope, receive, send)
            return
        scope.setdefault("state", {})["mcp_token"] = token
        await self.app(scope, receive, send)
```

The helper `_auth_response` returns the existing fixed `401` plus `WWW-Authenticate: Bearer`. Never include `str(error)` in HTTP responses or logs.

- [ ] **Step 6: Share the lifecycle API client with transport authentication**

Extend `LifecycleState` in `main.py` with `api_client: TicklyApiClient | None`. Set and clear it beside the HTTP client in the lifespan. Let `create_mcp_server` accept an optional lifecycle state so `create_http_app` can use the same state:

```python
async def verify_with_api(token: str, request_id: str) -> None:
    api_client = lifecycle_state.api_client
    if api_client is None:
        raise McpToolError("upstream_unavailable", "Tickly API 暂时不可用")
    await api_client.verify_token(token=token, request_id=request_id)

authenticated_app = ApiBearerMiddleware(
    protocol_app,
    verifier=verify_with_api,
    request_id_header=settings.request_id_header,
)
```

Keep `RequestIdMiddleware` outside `ApiBearerMiddleware` so the normalized request ID exists before upstream verification.

- [ ] **Step 7: Remove static configuration and update fakes**

Delete `token_sha256` and its validator/production requirement from MCP `Settings`. Remove only the readiness short-circuit that depended on the digest; `/ready` must still require an active lifecycle client and a successful API `/ready` response. Update `FakeApiClient` in `test_tools.py` with an async `verify_token` method so real Streamable HTTP tests use the same interface without changing the seven tool contracts.

- [ ] **Step 8: Run MCP tests and confirm GREEN**

Run the Step 3 command again.

Expected: PASS; invalid tokens are rejected before protocol parsing, valid tokens are verified upstream and every tool still forwards its current request Token and request ID.

- [ ] **Step 9: Run the API/MCP contract pair**

```powershell
mise exec -- pnpm test:api -- tests/test_mcp_dependencies.py tests/test_mcp_tasks_api.py -q
mise exec -- pnpm test:mcp -- tests/test_api_client.py tests/test_http_app.py tests/test_tools.py -q
```

Expected: both commands PASS with matching `401 authentication_required`, exact Bearer forwarding and request-ID propagation.

- [ ] **Step 10: Commit when explicitly authorized**

```powershell
git add -- apps/mcp/app/auth.py apps/mcp/app/api_client.py apps/mcp/app/middleware.py apps/mcp/app/main.py apps/mcp/app/config.py apps/mcp/tests/test_auth.py apps/mcp/tests/test_api_client.py apps/mcp/tests/test_http_app.py apps/mcp/tests/test_config.py apps/mcp/tests/test_tools.py
git commit -m "feat(mcp): 通过API验证用户Token"
```

### Task 8: Add the Web settings API client and password-change auth transition

**Files:**
- Create: `apps/web/src/features/settings/settings-api.ts`
- Create: `apps/web/src/features/settings/settings-api.test.ts`
- Modify: `apps/web/src/features/auth/auth-context.tsx`
- Modify: `apps/web/src/features/auth/auth-context.test.tsx`
- Modify: `apps/web/src/features/auth/login-form.tsx`
- Modify: `apps/web/src/features/auth/login-form.test.tsx`

- [ ] **Step 1: Write failing API-client tests**

Create `settings-api.test.ts` and verify method, body, authentication, one-time Token handling and stable errors:

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest"

import { setAccessToken } from "@/features/auth/auth-api"
import {
  changePassword,
  createMcpToken,
  listMcpTokens,
  revokeMcpToken,
} from "./settings-api"

beforeEach(() => {
  setAccessToken("web-access")
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe("settings API", () => {
  it("修改密码只发送当前密码和新密码", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetchMock)
    await changePassword("current password", "new password")
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/v1/account/password")
    expect(init.method).toBe("PUT")
    expect(JSON.parse(init.body)).toEqual({
      current_password: "current password",
      new_password: "new password",
    })
  })

  it("创建响应返回一次明文但不写入浏览器存储", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          item: {
            id: "token-id",
            name: "家中 Codex",
            status: "active",
            expires_at: null,
            last_used_at: null,
            created_at: "2026-08-25T00:00:00Z",
          },
          token: "tickly_mcp_token-id.secret",
        }),
      ),
    )
    const created = await createMcpToken("家中 Codex", null)
    expect(created.token).toBe("tickly_mcp_token-id.secret")
    expect(localStorage).toHaveLength(0)
    expect(sessionStorage).toHaveLength(0)
  })
})
```

Add list and revoke tests that assert `GET /api/v1/mcp-tokens` and `DELETE /api/v1/mcp-tokens/{id}`, and assert non-OK responses use `responseError` without exposing response internals.

- [ ] **Step 2: Write failing AuthContext notice tests**

Extend the test probe with a “密码修改完成” button calling `completePasswordChange()`. Assert it clears the access token, switches to anonymous state, and LoginForm renders only `密码已修改，请重新登录` as a status message.

- [ ] **Step 3: Run Web tests and confirm RED**

```powershell
mise exec -- pnpm --filter @tickly/web test -- src/features/settings/settings-api.test.ts src/features/auth/auth-context.test.tsx src/features/auth/login-form.test.tsx
```

Expected: FAIL because the settings client and password completion transition do not exist.

- [ ] **Step 4: Implement the settings API client**

Create `settings-api.ts`:

```typescript
import { apiFetch } from "@/features/auth/auth-api"
import { responseError } from "@/lib/api-error"

export type McpTokenStatus = "active" | "expired" | "revoked"
export type McpTokenExpiryDays = 90 | 365 | null

export type McpToken = {
  id: string
  name: string
  status: McpTokenStatus
  expires_at: string | null
  last_used_at: string | null
  created_at: string
}

export type IssuedMcpToken = {
  item: McpToken
  token: string
}

async function settingsRequest<T>(
  url: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await apiFetch(url, init)
  if (!response.ok) throw await responseError(response)
  return (await response.json()) as T
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const response = await apiFetch("/api/v1/account/password", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
  if (!response.ok) throw await responseError(response)
}

export async function listMcpTokens(): Promise<McpToken[]> {
  return (await settingsRequest<{ items: McpToken[] }>("/api/v1/mcp-tokens"))
    .items
}

export function createMcpToken(
  name: string,
  expiresInDays: McpTokenExpiryDays,
): Promise<IssuedMcpToken> {
  return settingsRequest("/api/v1/mcp-tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, expires_in_days: expiresInDays }),
  })
}

export async function revokeMcpToken(tokenId: string): Promise<void> {
  const response = await apiFetch(`/api/v1/mcp-tokens/${tokenId}`, {
    method: "DELETE",
  })
  if (!response.ok) throw await responseError(response)
}
```

- [ ] **Step 5: Add the safe password-completion state**

Extend `AuthState` with `notice?: string` on the anonymous branch and `AuthContextValue` with `completePasswordChange(): void`. Its implementation must only clear memory state:

```typescript
completePasswordChange() {
  setAccessToken(null)
  setState({
    status: "anonymous",
    notice: "密码已修改，请重新登录",
  })
},
```

Render `state.notice` in `LoginForm` as `role="status"`; do not merge it into the credential error or persist it. A later login attempt naturally replaces the anonymous state.

- [ ] **Step 6: Run Web tests and confirm GREEN**

Run the Step 3 command again.

Expected: PASS; no secret is persisted and successful password change has a safe one-session notice.

- [ ] **Step 7: Commit when explicitly authorized**

```powershell
git add -- apps/web/src/features/settings/settings-api.ts apps/web/src/features/settings/settings-api.test.ts apps/web/src/features/auth/auth-context.tsx apps/web/src/features/auth/auth-context.test.tsx apps/web/src/features/auth/login-form.tsx apps/web/src/features/auth/login-form.test.tsx
git commit -m "feat(web): 接入账号与MCP Token设置接口"
```

### Task 9: Add authenticated navigation and a shared application shell

**Files:**
- Create: `apps/web/src/features/auth/app-navigation.ts`
- Create: `apps/web/src/features/auth/app-navigation.test.tsx`
- Create: `apps/web/src/features/auth/authenticated-header.tsx`
- Modify: `apps/web/src/features/auth/authenticated-shell.tsx`
- Modify: `apps/web/src/features/tasks/todo-workspace.tsx`
- Modify: `apps/web/src/features/tasks/todo-workspace.test.tsx`
- Modify: `apps/web/src/features/auth/login-form.test.tsx`
- Modify: `apps/web/src/index.css`

- [ ] **Step 1: Write failing navigation and shell tests**

Test direct `/settings`, History navigation, back navigation and unknown paths:

```typescript
function NavigationProbe() {
  const { page, navigate } = useAppNavigation()
  return (
    <div>
      <span>{page}</span>
      <button type="button" onClick={() => navigate("settings")}>
        设置
      </button>
    </div>
  )
}

it("使用 History API 在任务和设置页之间导航", async () => {
  window.history.replaceState(null, "", "/")
  const user = userEvent.setup()
  render(<NavigationProbe />)
  await user.click(screen.getByRole("button", { name: "设置" }))
  expect(window.location.pathname).toBe("/settings")
  expect(screen.getByText("settings")).toBeInTheDocument()
  act(() => {
    window.history.back()
    window.dispatchEvent(new PopStateEvent("popstate"))
  })
  expect(screen.getByText("tasks")).toBeInTheDocument()
})
```

Update the authenticated App test to click “设置” and “返回待办”; assert the shared username/header stays mounted and Todo requests only happen on the task page.

- [ ] **Step 2: Run tests and confirm RED**

```powershell
mise exec -- pnpm --filter @tickly/web test -- src/features/auth/app-navigation.test.tsx src/features/auth/login-form.test.tsx src/features/tasks/todo-workspace.test.tsx
```

Expected: FAIL because there is no route state or shared header.

- [ ] **Step 3: Implement the two-route History adapter**

Create `app-navigation.ts`:

```typescript
import { useCallback, useEffect, useState } from "react"

export type AuthenticatedPage = "tasks" | "settings"

function pageFromPath(pathname: string): AuthenticatedPage {
  return pathname === "/settings" ? "settings" : "tasks"
}

export function useAppNavigation() {
  const [page, setPage] = useState<AuthenticatedPage>(() =>
    pageFromPath(window.location.pathname),
  )

  useEffect(() => {
    if (window.location.pathname !== "/" && window.location.pathname !== "/settings") {
      window.history.replaceState(null, "", "/")
    }
    const handlePopState = () => setPage(pageFromPath(window.location.pathname))
    window.addEventListener("popstate", handlePopState)
    return () => window.removeEventListener("popstate", handlePopState)
  }, [])

  const navigate = useCallback((next: AuthenticatedPage) => {
    const path = next === "settings" ? "/settings" : "/"
    if (window.location.pathname !== path) window.history.pushState(null, "", path)
    setPage(next)
  }, [])

  return { page, navigate }
}
```

- [ ] **Step 4: Lift the header into the authenticated shell**

Create `authenticated-header.tsx` with the current brand, username/timezone, logout state, and a page-aware navigation button. Make the brand a real button or link that calls `onNavigate("tasks")`; keep the other button labels explicit: `设置` on tasks and `返回待办` on settings. Test both brand activation and page button activation by keyboard/click so they share the History API adapter instead of forcing a document reload.

Update `AuthenticatedShell` to own navigation and render:

```typescript
<main className="todo-page">
  <section className="todo-shell">
    <AuthenticatedHeader
      page={page}
      username={state.user.username}
      timeZone={state.user.timezone}
      loggingOut={loggingOut}
      onNavigate={navigate}
      onLogout={handleLogout}
    />
    {page === "settings" ? (
      <section className="settings-page" aria-labelledby="settings-title">
        <h1 id="settings-title">设置</h1>
      </section>
    ) : (
      <TodoWorkspace timeZone={state.user.timezone} />
    )}
  </section>
</main>
```

During this task, render the settings heading inline in `AuthenticatedShell`; Task 10 replaces that inline section with the complete `SettingsPage` component. Remove `WorkspaceHeader`, username, logout and shell markup from `TodoWorkspace`, leaving it responsible only for task content and task timezone rendering.

- [ ] **Step 5: Add shared-shell CSS without changing the task layout**

Move the existing `.todo-header` and `.todo-account` styles to shared semantic class names only if necessary; preserve current desktop/mobile behavior. Add the minimal `.settings-page` layout class so direct `/settings` is usable before Task 10.

- [ ] **Step 6: Run tests and confirm GREEN**

Run the Step 2 command again.

Expected: PASS; `/settings` refresh, forward/back and task-page return work without a router dependency.

- [ ] **Step 7: Commit when explicitly authorized**

```powershell
git add -- apps/web/src/features/auth/app-navigation.ts apps/web/src/features/auth/app-navigation.test.tsx apps/web/src/features/auth/authenticated-header.tsx apps/web/src/features/auth/authenticated-shell.tsx apps/web/src/features/tasks/todo-workspace.tsx apps/web/src/features/tasks/todo-workspace.test.tsx apps/web/src/features/auth/login-form.test.tsx apps/web/src/index.css
git commit -m "feat(web): 增加设置页导航与共享外壳"
```

### Task 10: Build the password and MCP Token settings UI

**Files:**
- Create: `apps/web/src/features/settings/password-settings.tsx`
- Create: `apps/web/src/features/settings/mcp-token-settings.tsx`
- Create: `apps/web/src/features/settings/settings-page.tsx`
- Create: `apps/web/src/features/settings/settings-page.test.tsx`
- Modify: `apps/web/src/features/auth/authenticated-shell.tsx`
- Modify: `apps/web/src/index.css`

- [ ] **Step 1: Write failing password form tests**

Mock `settings-api` and AuthContext. Cover confirmation mismatch, request locking, safe API errors and completion:

```typescript
it("确认密码一致后修改密码并结束本地会话", async () => {
  changePasswordMock.mockResolvedValue(undefined)
  const user = userEvent.setup()
  render(<SettingsPage user={AUTH_USER} />)

  await user.type(screen.getByLabelText("当前密码"), "current password")
  await user.type(screen.getByLabelText("新密码"), "new password")
  await user.type(screen.getByLabelText("确认新密码"), "new password")
  await user.click(screen.getByRole("button", { name: "修改密码" }))

  expect(changePasswordMock).toHaveBeenCalledWith(
    "current password",
    "new password",
  )
  expect(auth.completePasswordChange).toHaveBeenCalledOnce()
})

it("确认密码不一致时不发送请求", async () => {
  const user = userEvent.setup()
  render(<SettingsPage user={AUTH_USER} />)
  await user.type(screen.getByLabelText("当前密码"), "current password")
  await user.type(screen.getByLabelText("新密码"), "new password")
  await user.type(screen.getByLabelText("确认新密码"), "different")
  await user.click(screen.getByRole("button", { name: "修改密码" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("两次输入的新密码不一致")
  expect(changePasswordMock).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Write failing Token management tests**

Cover independent list loading/error, create default 365, one-time dialog, clipboard, secret clearing, status rendering and confirmed revocation:

```typescript
it("创建Token后只在一次性Dialog显示并在关闭时清除", async () => {
  listMcpTokensMock.mockResolvedValue([])
  createMcpTokenMock.mockResolvedValue({ item: TOKEN_ITEM, token: RAW_TOKEN })
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  })
  const user = userEvent.setup()
  render(<SettingsPage user={AUTH_USER} />)

  await user.type(screen.getByLabelText("Token 名称"), "家中 Codex")
  await user.click(screen.getByRole("button", { name: "创建 Token" }))
  const dialog = await screen.findByRole("dialog", { name: "保存 MCP Token" })
  expect(dialog).toHaveTextContent(RAW_TOKEN)
  expect(createMcpTokenMock).toHaveBeenCalledWith("家中 Codex", 365)
  await user.click(screen.getByRole("button", { name: "复制 Token" }))
  expect(writeText).toHaveBeenCalledWith(RAW_TOKEN)
  await user.click(screen.getByRole("button", { name: "我已保存，关闭" }))
  expect(screen.queryByText(RAW_TOKEN)).not.toBeInTheDocument()
  expect(localStorage).toHaveLength(0)
  expect(sessionStorage).toHaveLength(0)
})
```

Add a revoke Dialog test that includes the Token name, disables repeat submission, calls `revokeMcpToken(id)`, updates the row to `revoked`, and restores the action on failure.

- [ ] **Step 3: Run UI tests and confirm RED**

```powershell
mise exec -- pnpm --filter @tickly/web test -- src/features/settings/settings-page.test.tsx
```

Expected: FAIL because the settings components do not exist.

- [ ] **Step 4: Implement the password card**

Create `password-settings.tsx` as a controlled form with `current-password`, `new-password`, and confirmation autocomplete semantics. The submit handler must be:

```typescript
async function handleSubmit(event: FormEvent<HTMLFormElement>) {
  event.preventDefault()
  if (submitting) return
  if (newPassword !== confirmation) {
    setError("两次输入的新密码不一致")
    return
  }
  setSubmitting(true)
  setError(null)
  try {
    await changePassword(currentPassword, newPassword)
    completePasswordChange()
  } catch (error) {
    setError(safeErrorMessage(error, "密码修改失败"))
  } finally {
    setSubmitting(false)
  }
}
```

Do not render passwords outside their input values and clear all three fields after any successful response before calling `completePasswordChange()`.

- [ ] **Step 5: Implement the Token card and one-time secret Dialog**

Create `mcp-token-settings.tsx` using `Dialog` from `@base-ui/react/dialog`. Keep raw Token state separate from list metadata:

```typescript
const [tokens, setTokens] = useState<McpToken[]>([])
const [issuedToken, setIssuedToken] = useState<string | null>(null)

function closeIssuedToken() {
  setIssuedToken(null)
}

async function createToken(event: FormEvent<HTMLFormElement>) {
  event.preventDefault()
  if (creating) return
  setCreating(true)
  setCreateError(null)
  try {
    const issued = await createMcpToken(name, expiresInDays)
    setTokens((current) => [issued.item, ...current])
    setIssuedToken(issued.token)
    setName("")
  } catch (error) {
    setCreateError(safeErrorMessage(error, "MCP Token 创建失败"))
  } finally {
    setCreating(false)
  }
}
```

The one-time Dialog must render the raw value, endpoint derived from `window.location.origin + "/mcp"`, environment-variable configuration guidance, copy feedback and an explicit close button. `onOpenChange(false)` must call `closeIssuedToken`; never place the raw value in an attribute, URL or persistent storage.

Load the list in an effect with an `active` guard. Render list errors with a retry button. Format timestamps in the authenticated user timezone using `Intl.DateTimeFormat`; render null `last_used_at` as `从未使用` and null expiry as `永不过期`.

- [ ] **Step 6: Implement confirmed, idempotent revocation**

Use a second Base UI Dialog. Store only the selected Token metadata, not a copied secret. On success update the matching row:

```typescript
setTokens((current) =>
  current.map((token) =>
    token.id === selected.id ? { ...token, status: "revoked" } : token,
  ),
)
```

Disable the confirm button while revoking, return focus to the originating button on close, and keep list/create/revoke errors independent.

- [ ] **Step 7: Compose the page and add responsive styles**

`settings-page.tsx` displays the read-only username/timezone summary, then `PasswordSettings` and `McpTokenSettings`. Replace the Task 9 inline settings section in `AuthenticatedShell` with this component.

Add focused Tailwind component classes in `index.css` for:

- a max-width settings content column;
- two independent bordered cards;
- consistent 44px minimum controls;
- desktop token rows and mobile stacked cards;
- `active`, `expired`, `revoked` status badges;
- one-time Token code wrapping without horizontal page overflow;
- reduced-motion behavior for the new dialogs.

- [ ] **Step 8: Run UI tests and confirm GREEN**

Run the Step 3 command again.

Expected: PASS for password change, independent errors, one-time secret clearing, clipboard feedback and revocation.

- [ ] **Step 9: Run the complete Web verification set**

```powershell
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm test:web
mise exec -- pnpm build
```

Expected: all commands exit 0.

- [ ] **Step 10: Commit when explicitly authorized**

```powershell
git add -- apps/web/src/features/settings/password-settings.tsx apps/web/src/features/settings/mcp-token-settings.tsx apps/web/src/features/settings/settings-page.tsx apps/web/src/features/settings/settings-page.test.tsx apps/web/src/features/auth/authenticated-shell.tsx apps/web/src/index.css
git commit -m "feat(web): 增加账号与MCP Token设置页"
```

### Task 11: Retire static MCP configuration and update deployment documentation

**Files:**
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `scripts/check-compose.ps1`
- Modify: `docs/development.md`
- Modify: `docs/mcp.md`
- Modify: `docs/mcp-client-deployment.md`

- [ ] **Step 1: Make the Compose boundary check fail on legacy static configuration**

Replace the shared-hash assertion in `scripts/check-compose.ps1` with an explicit retirement assertion:

```powershell
# 用户级 Token 只存在于 API 数据库和客户端；Compose 不得再注入共享摘要。
foreach ($serviceName in @("api", "mcp")) {
    $service = $config.services.PSObject.Properties[$serviceName].Value
    if ($null -ne $service.environment.PSObject.Properties["TICKLY_MCP_TOKEN_SHA256"]) {
        throw "$serviceName 不得继续配置静态 MCP Token 哈希"
    }
}
```

Run it before changing `compose.yaml`:

```powershell
$env:TICKLY_JWT_SECRET = "x" * 64
$env:TICKLY_MCP_ALLOWED_HOSTS = '["localhost:*"]'
$env:TICKLY_MCP_ALLOWED_ORIGINS = '["https://localhost:*"]'
$env:TICKLY_MCP_TOKEN_SHA256 = "0" * 64
mise exec -- pwsh -File scripts/check-compose.ps1
```

Expected: FAIL with `不得继续配置静态 MCP Token 哈希`.

- [ ] **Step 2: Remove the static environment contract**

Delete both `TICKLY_MCP_TOKEN_SHA256` entries from `compose.yaml` and delete the variable plus hash-generation comments from `.env.example`. Keep MCP allowed Host/Origin values mandatory in production.

- [ ] **Step 3: Rewrite current user documentation**

Update only current operational docs, not historical specs/plans:

- `docs/development.md`: create users with the existing CLI, log into `/settings`, create a user Token, configure the client raw Token.
- `docs/mcp.md`: remove server-side digest generation; document names, 90/365/no-expiry, one-time display, per-device revocation, and `codex mcp add ... --bearer-token-env-var TICKLY_MCP_TOKEN`.
- `docs/mcp-client-deployment.md`: remove shared-hash deployment/rotation steps; document migration, multi-user CLI creation, Web-issued Token rotation, account deactivation behavior and `401 authentication_required` troubleshooting.

Do not claim remote deployment, Package visibility or HTTPS smoke as completed.

- [ ] **Step 4: Run Compose and documentation checks**

```powershell
$env:TICKLY_JWT_SECRET = "x" * 64
$env:TICKLY_MCP_ALLOWED_HOSTS = '["localhost:*"]'
$env:TICKLY_MCP_ALLOWED_ORIGINS = '["https://localhost:*"]'
$env:TICKLY_IMAGE_TAG = "sha-local-verification"
$env:TICKLY_DOMAIN = "tickly.example.test"
$env:TICKLY_TRAEFIK_NETWORK = "traefik"
$env:TICKLY_TRAEFIK_ENTRYPOINT = "websecure"
$env:TICKLY_TRAEFIK_CERT_RESOLVER = "test"
docker compose config --quiet
docker compose -f compose.yaml -f compose.traefik.yaml config --quiet
mise exec -- pwsh -File scripts/check-compose.ps1
mise exec -- pwsh -File scripts/check-compose.ps1 -Traefik
rg -n "TICKLY_MCP_TOKEN_SHA256|mcp_account_unavailable|唯一账号" compose.yaml .env.example scripts/check-compose.ps1 docs/development.md docs/mcp.md docs/mcp-client-deployment.md
```

Expected: both Compose configs and both scripts exit 0; `rg` returns no matches in current configuration/docs.

- [ ] **Step 5: Commit when explicitly authorized**

```powershell
git add -- compose.yaml .env.example scripts/check-compose.ps1 docs/development.md docs/mcp.md docs/mcp-client-deployment.md
git commit -m "chore(deploy): 退役静态MCP Token配置"
```

### Task 12: Run full cross-application verification and inspect the final scope

**Files:**
- Verify only; modify a product file only if a failing check proves it belongs to this feature.

- [ ] **Step 1: Run all repository checks**

```powershell
mise exec -- pnpm check
```

Expected: Web lint/typecheck/build/Vitest, MCP pytest and API pytest all exit 0.

- [ ] **Step 2: Re-run the security-critical HTTP contract tests verbosely**

```powershell
mise exec -- pnpm test:api -- tests/test_account_api.py tests/test_mcp_tokens_api.py tests/test_mcp_dependencies.py tests/test_mcp_tasks_api.py -v
mise exec -- pnpm test:mcp -- tests/test_api_client.py tests/test_http_app.py tests/test_tools.py -v
```

Expected: PASS with two-user same-`serial` isolation, immediate revocation, exact Bearer propagation and stable request IDs.

- [ ] **Step 3: Run the two-real-CLI-user local HTTP smoke explicitly**

```powershell
mise exec -- pnpm test:api -- tests/test_mcp_tasks_api.py::test_cli_created_users_with_same_serial_are_isolated_over_http -v
```

Expected: PASS after exercising the actual CLI account entrypoint plus login, Web Token creation, public task creation and internal MCP HTTP reads for two accounts that both own `serial=1`.

- [ ] **Step 4: Validate Compose and build all images**

```powershell
$env:TICKLY_JWT_SECRET = "x" * 64
$env:TICKLY_MCP_ALLOWED_HOSTS = '["localhost:*"]'
$env:TICKLY_MCP_ALLOWED_ORIGINS = '["https://localhost:*"]'
$env:TICKLY_IMAGE_TAG = "sha-local-verification"
$env:TICKLY_DOMAIN = "tickly.example.test"
$env:TICKLY_TRAEFIK_NETWORK = "traefik"
$env:TICKLY_TRAEFIK_ENTRYPOINT = "websecure"
$env:TICKLY_TRAEFIK_CERT_RESOLVER = "test"
docker compose config --quiet
docker compose -f compose.yaml -f compose.traefik.yaml config --quiet
mise exec -- pwsh -File scripts/check-compose.ps1
mise exec -- pwsh -File scripts/check-compose.ps1 -Traefik
docker compose build api mcp web
```

Expected: all commands exit 0. Do not print expanded Compose JSON or real secrets.

- [ ] **Step 5: Inspect diff, generated artifacts and unrelated work**

```powershell
git status --short
git diff --check
git diff --stat
git diff --name-only
```

Expected: only files listed in this plan plus the approved spec/plan are changed. `apps/.pytest-release/` remains untracked and untouched; no database, `.env`, cache, build output or raw Token appears.

- [ ] **Step 6: Search for retired semantics and secret leakage risks**

```powershell
rg -n "TICKLY_MCP_TOKEN_SHA256|mcp_account_unavailable|只能创建一个账号|解析唯一启用账号" apps compose.yaml .env.example scripts docs/development.md docs/mcp.md docs/mcp-client-deployment.md
rg -n "localStorage|sessionStorage|IndexedDB" apps/web/src/features/settings apps/web/src/features/auth
```

Expected: the first search returns no current implementation/operational-doc matches; the second returns only tests asserting that secrets are not persisted, with no write calls in settings production code.

- [ ] **Step 7: Commit remaining integration-only corrections when explicitly authorized**

If all earlier tasks were committed and this task required a focused correction, stage only those exact paths and use:

```powershell
git commit -m "fix(mcp): 完善多用户认证集成边界"
```

If no correction was needed, do not create an empty commit.

---

## Final acceptance checklist

- [ ] Existing CLI creates two or more unique users; Web still has no registration path.
- [ ] Password change verifies the current password, clears the refresh Cookie, revokes refresh sessions and invalidates all old access tokens through `auth_version`.
- [ ] Each user can create multiple named Tokens with 90-day, 365-day or no expiry.
- [ ] Raw Token appears only in the create response and one-time Web Dialog.
- [ ] Revoked, expired and inactive-owner Tokens fail immediately with `401 authentication_required`.
- [ ] MCP validates before protocol parsing and API task routes revalidate before data access.
- [ ] Two users with the same task `serial` never read or mutate each other’s task.
- [ ] MCP has no SQLite access and no client-supplied user identifier.
- [ ] Static shared Token configuration is removed from API, MCP, Compose, checks and current docs.
- [ ] Web, API, MCP, Compose, Caddy and all three images pass the required verification.
