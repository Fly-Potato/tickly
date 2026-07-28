# Username and JWT Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace email with a normalized username and deliver the complete CLI, FastAPI JWT/refresh-session, React login, and Docker authentication flow defined by the approved stage 2 design.

**Architecture:** Keep a modular monolith: pure security helpers do not access the database, account/auth services own use cases and transactions, FastAPI routes own HTTP and Cookie behavior, and React keeps access tokens only in memory. Rewrite the initial migration because the repository has no historical data, and retain a single-account CLI boundary without adding repositories, public registration, client routing, or Todo behavior.

**Tech Stack:** Python 3.13, FastAPI, Pydantic Settings, SQLAlchemy 2.x, SQLite, Alembic, pwdlib with Argon2, PyJWT, pytest, React 19, TypeScript 6, Vite 8, Vitest 4, jsdom, Testing Library, Docker Compose, Node.js 24.

---

## Confirmed references

- Design: `docs/superpowers/specs/2026-07-28-username-jwt-auth-design.md`
- FastAPI current official security pattern: `PasswordHash.recommended()`, dummy password verification for unknown users, PyJWT `encode()` and decode with an explicit one-item algorithms allowlist.
- FastAPI Cookie pattern: set cookies on `Response`; use the same name/path/SameSite/Secure attributes when deleting.
- Vitest 4.1.6 official React pattern: `environment: "jsdom"` and `setupFiles` in `vitest.config.ts`.

## File map

### API and data

- Modify `apps/api/pyproject.toml` and `apps/api/uv.lock`: add `pwdlib[argon2]` and PyJWT.
- Modify `apps/api/app/core/config.py`: authentication settings and production secret validation.
- Create `apps/api/app/core/security.py`: username, password, JWT, and refresh digest primitives.
- Modify `apps/api/app/models/user.py`: username column and database invariants.
- Modify `apps/api/alembic/versions/0001_initial_schema.py`: replace email in the initial schema.
- Modify `apps/api/app/db/session.py`, `apps/api/app/main.py`, and `apps/api/app/core/errors.py`: application-bound request Session factory and stable SQLite busy errors.
- Create `apps/api/app/schemas/auth.py`: external authentication request/response types.
- Create `apps/api/app/services/accounts.py`: single-account CLI use cases.
- Create `apps/api/app/services/auth.py`: login, refresh, logout, and access-token use cases.
- Create `apps/api/app/api/dependencies.py`: request Session and active-user dependencies.
- Create `apps/api/app/api/routes/auth.py`; modify `apps/api/app/api/router.py`: HTTP and Cookie contract.
- Create `apps/api/app/cli.py`: standard-library CLI entrypoint.
- Add focused tests under `apps/api/tests/` and update existing model/migration/database tests.

### Web

- Modify `apps/web/package.json`, root `pnpm-lock.yaml`, `apps/web/tsconfig.node.json`, and root `package.json`: test dependencies and commands.
- Create `apps/web/vitest.config.ts` and `apps/web/src/test/setup.ts`: jsdom test environment.
- Create `apps/web/src/features/auth/auth-api.ts`: typed fetch operations and one-retry behavior.
- Create `apps/web/src/features/auth/auth-context.tsx`: in-memory token/user state and shared refresh Promise.
- Create `apps/web/src/features/auth/login-form.tsx`: accessible login interaction.
- Create `apps/web/src/features/auth/authenticated-shell.tsx`: protected placeholder shell.
- Modify `apps/web/src/App.tsx`: authentication state gate.
- Add focused Vitest files next to the authentication modules.

### Deployment and documentation

- Modify `apps/api/.env.example`, create root `.env.example`, and modify root `compose.yaml` and `scripts/docker-smoke.mjs`: development/production secret injection and real CLI/login smoke.
- Modify `docs/roadmaps/2026-07-26-tickly-zero-to-one.md`, `README.md`, and `AGENTS.md`: username contract, current status, commands, and verification expectations.

## Global execution rules

- Run commands from the repository root unless a step explicitly uses `workdir: apps/api`.
- Use `mise exec --` for pnpm and uv commands.
- Do not edit or regenerate unrelated files.
- Every behavior task follows RED → GREEN → focused regression → commit.
- Test comments, fixture descriptions, assertion messages, and docstrings are Chinese.
- Production security, transaction, replay, Cookie, and compatibility boundaries receive Chinese comments explaining why.
- Do not commit until the user explicitly requests a commit; the commit commands below define intended scopes and messages for that later authorization.

### Task 1: Replace email with the normalized username schema

**Files:**
- Modify: `apps/api/app/models/user.py`
- Modify: `apps/api/alembic/versions/0001_initial_schema.py`
- Modify: `apps/api/tests/test_models.py`
- Modify: `apps/api/tests/test_migrations.py`
- Modify: `apps/api/tests/test_database.py`

- [ ] **Step 1: Replace email expectations with failing username invariants**

Update every model-test `User` constructor so it uses `username`. Add this focused test:

```python
def test_username_constraints_reject_non_normalized_values(tmp_path: Path) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    invalid_usernames = ["ab", "A_user", "has space", "中文名", "a" * 33]

    for index, username in enumerate(invalid_usernames):
        with session_factory() as session:
            session.add(User(username=username, password_hash=f"hash-{index}"))
            with pytest.raises(IntegrityError):
                session.commit()

    engine.dispose()
```

Update the unique test to insert `person` twice. In the migration test, inspect `users` columns and CHECK constraints:

```python
user_columns = {column["name"] for column in inspector.get_columns("users")}
user_checks = {check["name"] for check in inspector.get_check_constraints("users")}
assert "username" in user_columns
assert "email" not in user_columns
assert {"ck_users_username_length", "ck_users_username_format"} <= user_checks
```

- [ ] **Step 2: Run the focused tests and observe RED**

Run from `apps/api`:

```bash
mise exec -- uv run pytest tests/test_models.py tests/test_migrations.py tests/test_database.py -q
```

Expected: failures report that `User` still requires/contains `email`, and migration columns do not contain `username`.

- [ ] **Step 3: Implement the username column and initial migration**

In `User`, add table constraints and replace the column:

```python
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "length(username) BETWEEN 3 AND 32",
            name="ck_users_username_length",
        ),
        CheckConstraint(
            "username = lower(username) "
            "AND username NOT GLOB '*[^a-z0-9_-]*'",
            name="ck_users_username_format",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
```

Mirror the same `VARCHAR(32)`, unique constraint, and named CHECK constraints in `0001_initial_schema.py`. Replace every test fixture and persistence assertion from email to username; do not add a second migration.

- [ ] **Step 4: Run focused and full API tests**

```bash
mise exec -- uv run pytest tests/test_models.py tests/test_migrations.py tests/test_database.py -q
mise exec -- pnpm test:api
```

Expected: all tests pass and no test or current schema contains a functional `email` field.

- [ ] **Step 5: Prepare the intended commit scope**

```bash
git add -- apps/api/app/models/user.py apps/api/alembic/versions/0001_initial_schema.py apps/api/tests/test_models.py apps/api/tests/test_migrations.py apps/api/tests/test_database.py
git commit -m "refactor(api): 使用用户名替代邮箱标识"
```

### Task 2: Add authentication dependencies and typed configuration

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/tests/test_config.py`
- Modify: `apps/api/.env.example`

- [ ] **Step 1: Add failing configuration tests**

Add tests for defaults, environment overrides, and production rejection:

```python
def test_authentication_defaults_are_explicit() -> None:
    settings = Settings(_env_file=None)
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_issuer == "tickly-api"
    assert settings.jwt_audience == "tickly-web"
    assert settings.access_token_minutes == 15
    assert settings.refresh_token_days == 30
    assert settings.refresh_cookie_name == "tickly_refresh"
    assert settings.refresh_cookie_secure is False


def test_production_rejects_development_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(environment=Environment.PRODUCTION, _env_file=None)
```

- [ ] **Step 2: Observe the missing settings**

Run from `apps/api`:

```bash
mise exec -- uv run pytest tests/test_config.py -q
```

Expected: authentication attributes are missing and production validation does not fail.

- [ ] **Step 3: Add the direct dependencies**

```bash
mise exec -- uv add --project apps/api "pwdlib[argon2]" pyjwt
```

Expected: `pyproject.toml` declares both runtime dependencies and `uv.lock` updates once.

- [ ] **Step 4: Implement settings and production validation**

Add these fields to `Settings`:

```python
jwt_secret: str = "development-only-change-me"
jwt_algorithm: Literal["HS256"] = "HS256"
jwt_issuer: str = "tickly-api"
jwt_audience: str = "tickly-web"
access_token_minutes: int = 15
refresh_token_days: int = 30
refresh_cookie_name: str = "tickly_refresh"
refresh_cookie_secure: bool = False
```

Add a model validator that rejects production when the secret equals the development value, is shorter than 32 characters, or `refresh_cookie_secure` is false. Add positive integer validators for both lifetimes. Extend `.env.example` with names and non-sensitive development examples; never add a production secret.

- [ ] **Step 5: Verify configuration and lock consistency**

```bash
mise exec -- uv lock --project apps/api --check
mise exec -- uv run --project apps/api pytest apps/api/tests/test_config.py -q
```

Expected: all configuration tests pass and the lockfile is current.

- [ ] **Step 6: Prepare the intended commit scope**

```bash
git add -- apps/api/pyproject.toml apps/api/uv.lock apps/api/app/core/config.py apps/api/tests/test_config.py apps/api/.env.example
git commit -m "feat(api): 增加认证安全配置"
```

### Task 3: Implement username, password, JWT, and refresh digest primitives

**Files:**
- Create: `apps/api/app/core/security.py`
- Create: `apps/api/tests/test_security.py`

- [ ] **Step 1: Write focused failing security tests**

Cover the public contract with these exact cases:

```python
@pytest.mark.parametrize(
    ("raw", "normalized"),
    [(" Potato ", "potato"), ("user_01", "user_01"), ("a-b", "a-b")],
)
def test_normalize_username(raw: str, normalized: str) -> None:
    assert normalize_username(raw) == normalized


@pytest.mark.parametrize("raw", ["ab", "has space", "中文名", "a" * 33])
def test_normalize_username_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(InvalidUsername):
        normalize_username(raw)


def test_password_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_access_and_refresh_tokens_enforce_type_and_sid(settings: Settings) -> None:
    access = issue_access_token("user-id", settings)
    refresh = issue_refresh_token("user-id", "session-id", settings)
    assert decode_token(access, "access", settings).sub == "user-id"
    assert decode_token(refresh, "refresh", settings).sid == "session-id"
    with pytest.raises(InvalidToken):
        decode_token(access, "refresh", settings)
```

Also cover expired tokens, wrong issuer, wrong audience, wrong signature, missing required claims, fixed algorithm rejection, password length, SHA-256 refresh digests, and constant-time equality behavior.

- [ ] **Step 2: Observe RED**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_security.py -q
```

Expected: import failure for `app.core.security`.

- [ ] **Step 3: Implement the pure security module**

Define these stable public exception and payload types:

```python
class InvalidUsername(ValueError):
    """用户名不符合规范化规则。"""


class InvalidPassword(ValueError):
    """密码不符合最小安全边界。"""


class InvalidToken(ValueError):
    """JWT 未通过固定算法、claims 或类型校验。"""

class TokenPayload(BaseModel):
    sub: str
    jti: str
    type: Literal["access", "refresh"]
    iss: str
    aud: str | list[str]
    iat: datetime
    exp: datetime
    sid: str | None = None
```

Expose the exact function signatures `normalize_username(value: str) -> str`, `validate_password(value: str) -> str`, `hash_password(value: str) -> str`, `verify_password(value: str, encoded: str) -> bool`, `verify_dummy_password(value: str) -> None`, `issue_access_token(user_id: str, settings: Settings) -> str`, `issue_refresh_token(user_id: str, session_id: str, settings: Settings, *, expires_at: datetime | None = None) -> str`, `decode_token(token: str, expected_type: Literal["access", "refresh"], settings: Settings) -> TokenPayload`, `digest_refresh_token(token: str) -> str`, and `refresh_digest_matches(token: str, digest: str) -> bool`.

Use `PasswordHash.recommended()` once at module scope and precompute one dummy hash. Decode with `algorithms=[settings.jwt_algorithm]`, issuer, audience, and `options={"require": ["sub", "jti", "type", "iss", "aud", "iat", "exp"]}`. Require `sid` only for refresh tokens. Preserve only domain-safe exception types.

- [ ] **Step 4: Verify the security boundary**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_security.py -q
```

Expected: all security cases pass without logging plaintext passwords, hashes, or tokens.

- [ ] **Step 5: Prepare the intended commit scope**

```bash
git add -- apps/api/app/core/security.py apps/api/tests/test_security.py
git commit -m "feat(api): 实现密码与 JWT 安全基础"
```

### Task 4: Bind request Sessions and map SQLite busy errors

**Files:**
- Modify: `apps/api/app/db/session.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/core/errors.py`
- Create: `apps/api/app/api/dependencies.py`
- Modify: `apps/api/tests/test_database.py`
- Modify: `apps/api/tests/test_errors.py`

- [ ] **Step 1: Add a failing injected-Engine request test**

Create a temporary migrated database, inject its Engine into `create_app()`, add a temporary route with `DbSession`, insert a marker row, and assert the row exists in the injected database rather than the default development database.

```python
def migrate_to_head(database_path: Path) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")


def test_request_session_uses_application_engine(tmp_path: Path) -> None:
    database_path = tmp_path / "request.db"
    migrate_to_head(database_path)
    settings = make_settings(database_path)
    engine = create_engine_for_settings(settings)
    app = create_app(settings, database_engine=engine)

    @app.post("/session-marker")
    def create_marker(session: DbSession) -> dict[str, str]:
        session.add(User(username="marker", password_hash="hash"))
        session.commit()
        return {"status": "created"}

    with TestClient(app) as client:
        assert client.post("/session-marker").status_code == 200

    with create_session_factory(engine)() as session:
        assert session.scalar(select(User.username)) == "marker"
```

- [ ] **Step 2: Observe RED against the global SessionLocal**

Run from `apps/api`:

```bash
mise exec -- uv run pytest tests/test_database.py::test_request_session_uses_application_engine -q
```

Expected: the request dependency does not resolve against the injected Engine.

- [ ] **Step 3: Replace the global request dependency**

In `create_app()`, assign:

```python
application.state.database_session_factory = create_session_factory(
    resolved_database_engine
)
```

In `api/dependencies.py`, define:

```python
def get_db_session(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.database_session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db_session)]
```

Remove the import-time `engine` and `SessionLocal` globals from `db/session.py`; the CLI will create its own Engine and Session factory explicitly.

- [ ] **Step 4: Add and implement the stable database-busy mapping**

Add a route in `test_errors.py` that raises `sqlalchemy.exc.OperationalError` whose original exception is `sqlite3.OperationalError("database is locked")`. First assert RED against the current generic 500 handler, then register a dedicated handler that returns:

```json
{
  "error": {
    "code": "database_busy",
    "message": "数据库繁忙，请稍后重试",
    "request_id": "busy-request",
    "details": []
  }
}
```

The handler returns `503` only when `exc.orig` is a SQLite operational error containing `database is locked` or `database table is locked`; all other operational errors continue through the sanitized `500 internal_error` path. Do not return SQL or exception text.

- [ ] **Step 5: Verify request isolation and full API regression**

```bash
mise exec -- pnpm test:api
```

Expected: all existing and new API tests pass; importing the Session module no longer opens the default SQLite database.

- [ ] **Step 6: Prepare the intended commit scope**

```bash
git add -- apps/api/app/db/session.py apps/api/app/main.py apps/api/app/core/errors.py apps/api/app/api/dependencies.py apps/api/tests/test_database.py apps/api/tests/test_errors.py
git commit -m "refactor(api): 绑定请求会话与数据库繁忙错误"
```

### Task 5: Implement single-account services and CLI

**Files:**
- Create: `apps/api/app/services/__init__.py`
- Create: `apps/api/app/services/accounts.py`
- Create: `apps/api/app/cli.py`
- Create: `apps/api/tests/test_accounts.py`
- Create: `apps/api/tests/test_cli.py`

- [ ] **Step 1: Write failing account-service tests**

Cover create, second-account refusal, password change, deactivation, and session revocation with a migrated temporary file database. Assert password change and deactivation revoke all active `AuthSession` rows in the same committed state.

```python
def test_create_account_normalizes_username_and_refuses_second_user(session: Session) -> None:
    user = create_account(session, " Potato ", "correct horse battery staple")
    assert user.username == "potato"
    assert verify_password("correct horse battery staple", user.password_hash)
    with pytest.raises(AccountAlreadyExists):
        create_account(session, "second", "another correct password")
```

- [ ] **Step 2: Observe RED**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_accounts.py -q
```

Expected: import failure for `app.services.accounts`.

- [ ] **Step 3: Implement account use cases**

Define `AccountAlreadyExists` and `AccountNotFound` as distinct exception classes with Chinese docstrings. Expose these exact service signatures:

- `create_account(session: Session, username: str, password: str) -> User`
- `change_password(session: Session, username: str, password: str) -> User`
- `deactivate_account(session: Session, username: str) -> User`
- `revoke_all_sessions(session: Session, username: str) -> int`

Each public function normalizes the username and owns commit/rollback. `create_account` starts a SQLite `BEGIN IMMEDIATE` transaction before counting users so two concurrent CLI processes cannot both pass the single-account check. `change_password` updates the Argon2 hash and sets `revoked_at` for all active sessions before one commit. `deactivate_account` sets `is_active=False` and revokes sessions before one commit. Use `select()` and `update()`, never `Session.query()`.

- [ ] **Step 4: Write failing CLI behavior tests**

Patch `getpass.getpass`, invoke `main(argv)`, and assert exact exit codes without passing plaintext passwords in argv. Cover mismatched confirmation, second account, confirmation username for deactivation, and sanitized error output.

```python
def test_create_cli_reads_password_twice(monkeypatch, cli_database_url, capsys) -> None:
    answers = iter(["correct horse battery staple", "correct horse battery staple"])
    monkeypatch.setattr("getpass.getpass", lambda _: next(answers))
    assert main(["user", "create", "--username", "Potato"]) == 0
    assert "账号已创建" in capsys.readouterr().out
```

- [ ] **Step 5: Implement the standard-library CLI**

Build `argparse` subcommands for the four approved commands. `main(argv: Sequence[str] | None = None) -> int` creates Settings, Engine, and Session factory explicitly; `if __name__ == "__main__": raise SystemExit(main())` provides the module entrypoint. Use two `getpass()` calls for create/change-password and one `input()` username confirmation for deactivate. Catch only known domain/validation errors and emit Chinese safe messages to stderr.

- [ ] **Step 6: Verify services and CLI**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_accounts.py apps/api/tests/test_cli.py -q
```

Expected: all cases pass and captured output contains no password, hash, token, database URL, SQL, or traceback.

- [ ] **Step 7: Prepare the intended commit scope**

```bash
git add -- apps/api/app/services/__init__.py apps/api/app/services/accounts.py apps/api/app/cli.py apps/api/tests/test_accounts.py apps/api/tests/test_cli.py
git commit -m "feat(api): 增加单账号管理 CLI"
```

### Task 6: Implement authentication schemas and service transactions

**Files:**
- Create: `apps/api/app/schemas/__init__.py`
- Create: `apps/api/app/schemas/auth.py`
- Create: `apps/api/app/services/auth.py`
- Create: `apps/api/tests/test_auth_service.py`

- [ ] **Step 1: Write failing service tests**

Use a migrated file database and explicit Settings. Cover successful login, unknown-user dummy verification, wrong password, inactive account, fixed 30-day session expiry, successful refresh rotation, conditional-update replay detection, logout idempotency, and access-token user lookup.

```python
def test_refresh_rotates_digest_without_extending_session(session, settings) -> None:
    login = login_user(session, "potato", PASSWORD, settings, user_agent="pytest")
    original_expiry = login.session.expires_at
    rotated = refresh_session(session, login.refresh_token, settings)
    assert rotated.refresh_token != login.refresh_token
    assert rotated.session.expires_at == original_expiry
    assert rotated.session.refresh_token_hash == digest_refresh_token(
        rotated.refresh_token
    )
```

- [ ] **Step 2: Observe RED**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_auth_service.py -q
```

Expected: import failure for `app.services.auth`.

- [ ] **Step 3: Create external schemas**

Define:

```python
class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def normalize_login_username(cls, value: str) -> str:
        return normalize_username(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class CurrentUserResponse(BaseModel):
    id: str
    username: str
    timezone: str
    is_active: bool
```

Do not expose ORM models or refresh tokens in response schemas.

- [ ] **Step 4: Implement service results, exceptions, and transactions**

Define the four domain exceptions as separate classes with Chinese docstrings. Define the immutable service result exactly once:

```python
@dataclass(frozen=True)
class AuthenticationResult:
    access_token: str
    refresh_token: str
    session: AuthSession
    expires_in: int
```

Expose `login_user(session: Session, username: str, password: str, settings: Settings, *, user_agent: str | None) -> AuthenticationResult`, `refresh_session(session: Session, refresh_token: str, settings: Settings) -> AuthenticationResult`, `logout_session(session: Session, refresh_token: str | None, settings: Settings) -> None`, and `authenticate_access_token(session: Session, access_token: str, settings: Settings) -> User`.

Login must perform dummy verification when no user exists. Store at most the first 512 characters of a non-empty `user_agent`. Refresh must decode first, verify `sub` and `sid`, and consume the old digest with a conditional SQLAlchemy `update()` whose WHERE includes `id`, `refresh_token_hash`, `revoked_at IS NULL`, and `expires_at > now`. A zero row count triggers a second lookup; an existing session is revoked and `RefreshReplayed` is raised. Rotation reuses the original `expires_at`.

- [ ] **Step 5: Verify service behavior and rollback**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_auth_service.py -q
```

Expected: all login, refresh, replay, logout, fixed-expiry, and rollback cases pass.

- [ ] **Step 6: Prepare the intended commit scope**

```bash
git add -- apps/api/app/schemas/__init__.py apps/api/app/schemas/auth.py apps/api/app/services/auth.py apps/api/tests/test_auth_service.py
git commit -m "feat(api): 实现认证与会话轮换服务"
```

### Task 7: Expose login, refresh, logout, and current-user APIs

**Files:**
- Modify: `apps/api/app/api/dependencies.py`
- Create: `apps/api/app/api/routes/auth.py`
- Modify: `apps/api/app/api/router.py`
- Create: `apps/api/tests/test_auth_api.py`

- [ ] **Step 1: Write failing HTTP contract tests**

Build `create_auth_client()` with a migrated temporary database and injected Engine. Cover:

```python
def test_login_sets_refresh_cookie_and_returns_access_token(auth_client) -> None:
    response = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "Potato", "password": PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    cookie = response.headers["set-cookie"]
    assert "tickly_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie
```

Also assert unified login failure, missing/invalid access, `/me`, successful refresh and cookie replacement, replay response and revocation, logout idempotency, Cookie clearing, request IDs, and no secret leakage.

- [ ] **Step 2: Observe RED routes**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_auth_api.py -q
```

Expected: `/api/v1/auth/*` returns `404 not_found`.

- [ ] **Step 3: Implement bearer and active-user dependencies**

Use `HTTPBearer(auto_error=False)`. `get_current_user` decodes an access token, loads the user by immutable `sub`, and rejects missing, invalid, or inactive users with:

```python
raise AppError(
    status_code=status.HTTP_401_UNAUTHORIZED,
    code="authentication_required",
    message="需要登录",
)
```

Expose `CurrentUser = Annotated[User, Depends(get_current_user)]`.

- [ ] **Step 4: Implement routes and Cookie helpers**

Create `APIRouter(prefix="/auth", tags=["auth"])`. Implement typed synchronous route functions for `/login`, `/refresh`, `/logout`, and `/me`. Centralize Cookie setting/deletion so both use `settings.refresh_cookie_name`, path `/api/v1/auth`, `httponly=True`, configured Secure, and `samesite="strict"`. Map only known service exceptions to the approved AppError codes.

- [ ] **Step 5: Register and verify the router**

```python
from app.api.routes.auth import router as auth_router

api_router = APIRouter()
api_router.include_router(auth_router)
```

Run:

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_auth_api.py -q
mise exec -- pnpm test:api
```

Expected: all API tests pass; OpenAPI contains the four auth operations; `/health` remains database-independent.

- [ ] **Step 6: Prepare the intended commit scope**

```bash
git add -- apps/api/app/api/dependencies.py apps/api/app/api/routes/auth.py apps/api/app/api/router.py apps/api/tests/test_auth_api.py
git commit -m "feat(api): 提供 JWT 认证接口"
```

### Task 8: Establish Vitest 4 and jsdom

**Files:**
- Modify: `apps/web/package.json`
- Modify: `package.json`
- Modify: `pnpm-lock.yaml`
- Modify: `apps/web/tsconfig.node.json`
- Create: `apps/web/vitest.config.ts`
- Create: `apps/web/src/test/setup.ts`
- Create: `apps/web/src/test/smoke.test.tsx`

- [ ] **Step 1: Add test dependencies and scripts**

```bash
mise exec -- pnpm --filter @tickly/web add -D vitest@^4 jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom
```

Add Web script `"test": "vitest run"`, root script `"test:web": "pnpm --filter @tickly/web test"`, and insert `pnpm test:web` before `pnpm test:api` in root `check`.

- [ ] **Step 2: Add config and one failing smoke test**

Create `vitest.config.ts`:

```typescript
import path from "node:path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
})
```

Create setup:

```typescript
import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

afterEach(() => cleanup())
```

Add `vitest.config.ts` to `tsconfig.node.json` includes. Write a smoke test rendering the current App and asserting its heading.

- [ ] **Step 3: Run Web tests and type checks**

```bash
mise exec -- pnpm test:web
mise exec -- pnpm typecheck
```

Expected: the smoke test passes, and Vitest config/setup are type-safe without global test APIs.

- [ ] **Step 4: Prepare the intended commit scope**

```bash
git add -- apps/web/package.json package.json pnpm-lock.yaml apps/web/tsconfig.node.json apps/web/vitest.config.ts apps/web/src/test/setup.ts apps/web/src/test/smoke.test.tsx
git commit -m "test(web): 建立 Vitest 认证测试环境"
```

### Task 9: Implement the in-memory Web authentication state

**Files:**
- Create: `apps/web/src/features/auth/auth-api.ts`
- Create: `apps/web/src/features/auth/auth-context.tsx`
- Create: `apps/web/src/features/auth/auth-context.test.tsx`
- Modify: `apps/web/src/main.tsx`

- [ ] **Step 1: Write failing state-flow tests**

Mock `fetch` and cover initialization refresh success/failure, login, logout, shared refresh for concurrent requests, retry exactly once, and persistent-storage absence.

```typescript
it("shares one refresh request across concurrent authenticated requests", async () => {
  const first = apiFetch("/api/v1/example")
  const second = apiFetch("/api/v1/example-2")
  await Promise.all([first, second])
  expect(refreshCalls).toBe(1)
})
```

- [ ] **Step 2: Observe RED**

```bash
mise exec -- pnpm test:web -- auth-context.test.tsx
```

Expected: authentication modules do not exist.

- [ ] **Step 3: Implement typed API operations**

Define `AuthUser`, `TokenResponse`, and `ApiError`. Use relative `/api/v1/auth/*` URLs and `credentials: "same-origin"`. Keep the access token in a module-private variable reachable only through explicit setters used by the provider. Maintain one module-private `refreshPromise`; login, refresh, and logout bypass automatic retry. Other requests retry once only after `authentication_required`.

- [ ] **Step 4: Implement the provider state machine**

Expose:

```typescript
type AuthState =
  | { status: "initializing" }
  | { status: "anonymous"; error?: string }
  | { status: "authenticated"; user: AuthUser }

type AuthContextValue = {
  state: AuthState
  login(username: string, password: string): Promise<void>
  logout(): Promise<void>
}
```

On mount, refresh then call `/me`; on failure enter anonymous. On logout, clear memory state even if the network request fails. Wrap `<App />` with `<AuthProvider>` in `main.tsx`.

- [ ] **Step 5: Verify the state boundary**

```bash
mise exec -- pnpm test:web -- auth-context.test.tsx
mise exec -- pnpm typecheck
```

Expected: concurrent requests share one refresh, requests retry at most once, and storage assertions remain empty.

- [ ] **Step 6: Prepare the intended commit scope**

```bash
git add -- apps/web/src/features/auth/auth-api.ts apps/web/src/features/auth/auth-context.tsx apps/web/src/features/auth/auth-context.test.tsx apps/web/src/main.tsx
git commit -m "feat(web): 增加内存认证状态"
```

### Task 10: Build the login and protected shell UI

**Files:**
- Create: `apps/web/src/features/auth/login-form.tsx`
- Create: `apps/web/src/features/auth/login-form.test.tsx`
- Create: `apps/web/src/features/auth/authenticated-shell.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/index.css`

- [ ] **Step 1: Write failing user-interaction tests**

Use Testing Library and `userEvent` to cover username/password labels, Enter submission, disabled submitting state, unified failure message, authenticated username display, logout, and initialization UI.

```typescript
it("submits normalized credentials and disables duplicate submission", async () => {
  const user = userEvent.setup()
  render(<LoginForm />)
  await user.type(screen.getByLabelText("用户名"), "Potato")
  await user.type(screen.getByLabelText("密码"), PASSWORD)
  await user.click(screen.getByRole("button", { name: "登录" }))
  expect(login).toHaveBeenCalledWith("Potato", PASSWORD)
})
```

- [ ] **Step 2: Observe RED**

```bash
mise exec -- pnpm test:web -- login-form.test.tsx
```

Expected: login components do not exist.

- [ ] **Step 3: Implement the minimal accessible UI**

Use native `<label>` and `<input>` elements plus the existing Button component. Password uses `type="password"` and `autoComplete="current-password"`; username uses `autoComplete="username"`. Use a form submit handler for keyboard behavior. The authenticated shell only shows the username, “认证已就绪”, and a logout button; do not add Todo placeholders or client routing.

- [ ] **Step 4: Verify Web behavior and production build**

```bash
mise exec -- pnpm test:web
mise exec -- pnpm lint
mise exec -- pnpm typecheck
mise exec -- pnpm build
```

Expected: all Web tests and checks pass with loading, error, disabled, and keyboard states covered.

- [ ] **Step 5: Prepare the intended commit scope**

```bash
git add -- apps/web/src/features/auth/login-form.tsx apps/web/src/features/auth/login-form.test.tsx apps/web/src/features/auth/authenticated-shell.tsx apps/web/src/App.tsx apps/web/src/index.css
git commit -m "feat(web): 完成用户名登录闭环"
```

### Task 11: Add production secret injection and authenticated Docker smoke

**Files:**
- Modify: `compose.yaml`
- Modify: `scripts/docker-smoke.mjs`
- Modify: `apps/api/.env.example`
- Create: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Make Compose require the production secret**

Add to the API environment:

```yaml
TICKLY_JWT_SECRET: ${TICKLY_JWT_SECRET:?TICKLY_JWT_SECRET is required}
TICKLY_REFRESH_COOKIE_SECURE: ${TICKLY_REFRESH_COOKIE_SECURE:-true}
```

Run without a secret and expect `docker compose config --quiet` to fail with the required-variable message.

- [ ] **Step 2: Extend the smoke runner input boundary**

At smoke startup, set a random secret only for the child Compose project:

```javascript
import { randomBytes } from "node:crypto"

process.env.TICKLY_JWT_SECRET = randomBytes(32).toString("hex")
```

Extend `run()` with an optional `input` string and use piped stdin for CLI password entry; never put the password in argv or command logs.

- [ ] **Step 3: Add CLI and HTTP assertions**

After migration and before `compose up`, run the API image CLI with `-T` and send the password twice over stdin. After health checks, POST username/password JSON to `/api/v1/auth/login`, assert the `Set-Cookie` header includes `HttpOnly`, `Secure`, `SameSite=Strict`, and the auth path, call `/api/v1/auth/me` with the returned Bearer token, and assert the username. Node may send the Cookie header explicitly when testing refresh over the HTTP-only smoke endpoint; do not weaken the production `Secure` setting. Keep the fixed smoke password confined to Node process memory and the disposable volume.

- [ ] **Step 4: Run the real cross-platform smoke**

```bash
mise exec -- pnpm docker:smoke
```

Expected: output ends with `Docker smoke test passed`; migration, CLI creation, login, Cookie, `/me`, health/readiness, non-root, and unpublished API port all pass; temporary resources are removed.

- [ ] **Step 5: Document secret and local behavior**

Update `apps/api/.env.example` for local API development. Create root `.env.example` with variable names and explicit replace-me values for Compose. Update README with CLI commands, username login, local migration recreation, development JWT configuration, and production secret requirement. Do not include a reusable production secret.

- [ ] **Step 6: Prepare the intended commit scope**

```bash
git add -- compose.yaml scripts/docker-smoke.mjs apps/api/.env.example .env.example README.md
git commit -m "test(docker): 覆盖账号创建与登录冒烟"
```

### Task 12: Calibrate roadmap and collaboration documentation

**Files:**
- Modify: `docs/roadmaps/2026-07-26-tickly-zero-to-one.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Verify: `docs/superpowers/specs/2026-07-28-username-jwt-auth-design.md`

- [ ] **Step 1: Replace the confirmed product terminology**

Change email-login statements, `users.email`, login payload descriptions, dummy-hash wording, and test strategy to username. Mark stage 2 complete only after every stage 2 acceptance command passes; do not describe Todo or AI as implemented.

- [ ] **Step 2: Update verification rules**

Document `test:web`, the expanded root `check`, authentication changes requiring both API and Web tests, and Docker/auth changes requiring `docker:smoke`. Preserve existing Chinese comment rules and scope boundaries.

- [ ] **Step 3: Verify documentation facts and Markdown**

```bash
rg -n "邮箱|email" README.md AGENTS.md docs/roadmaps docs/superpowers/specs/2026-07-28-username-jwt-auth-design.md
rg -n "test:web|docker:smoke|username|用户名" package.json apps/web/package.json README.md AGENTS.md docs/roadmaps
git diff --check
```

Expected: remaining email references only explain the deliberate replacement/non-goal; commands and implementation status match manifests and code.

- [ ] **Step 4: Prepare the intended commit scope**

```bash
git add -- docs/roadmaps/2026-07-26-tickly-zero-to-one.md AGENTS.md README.md docs/superpowers/specs/2026-07-28-username-jwt-auth-design.md
git commit -m "docs: 校准用户名认证阶段说明"
```

### Task 13: Final stage 2 verification

**Files:**
- Verify all files changed by Tasks 1–12.

- [ ] **Step 1: Reinstall from locks**

```bash
mise exec -- pnpm install --frozen-lockfile
mise exec -- uv sync --project apps/api --locked
```

Expected: both package managers complete without changing lockfiles.

- [ ] **Step 2: Run the full local quality gate**

```bash
mise exec -- pnpm check
```

Expected: Web lint, Web typecheck, Web build, Web Vitest, and all API pytest tests exit zero.

- [ ] **Step 3: Run migration round-trip and Docker smoke**

```bash
mise exec -- uv run --project apps/api pytest apps/api/tests/test_migrations.py -q
mise exec -- pnpm docker:smoke
```

Expected: initial username schema upgrades/downgrades cleanly and authenticated Docker smoke passes with cleanup.

- [ ] **Step 4: Audit secrets, browser storage, and public contracts**

```bash
rg -n "localStorage|sessionStorage|password_hash|refresh_token|TICKLY_JWT_SECRET" apps README.md compose.yaml
git diff --check
git status --short
```

Expected: token storage appears only in negative tests/design constraints, password hashes and refresh tokens are not response fields/logs, no secret value is committed, diff check passes, and status contains only the intended stage 2 scope.

- [ ] **Step 5: Review the final staged scope before any authorized commit**

```bash
git diff --stat
git diff --name-status
git diff
```

Expected: no Todo API, AI, registration, display-name, routing-library, repository-layer, or unrelated changes.
