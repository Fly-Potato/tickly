# Tickly Database Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the SQLAlchemy 2.x, SQLite, and Alembic foundation for the future authentication and Todo APIs.

**Architecture:** Keep synchronous SQLAlchemy inside `apps/api`. `app/db` owns Engine, Session factory, SQLite connection settings, and FastAPI session dependency; `app/models` owns typed ORM models; Alembic imports shared metadata and never runs `create_all()` at application startup.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x, Alembic, SQLite, FastAPI, pytest, uv.

## Global Constraints

- Keep the change inside `apps/api` plus the stage 1 design and plan documents.
- Use `DeclarativeBase`, `Mapped`, `mapped_column`, and `select()` style.
- Use a file-backed SQLite database for integration tests.
- Enable `foreign_keys=ON`, WAL, and busy timeout.
- Keep schema changes in Alembic; never call `create_all()` from app startup.
- Do not implement authentication routes, account CLI, Todo routes, AI, or a repository abstraction.

---

### Task 1: Database dependency and settings

**Files:** `apps/api/pyproject.toml`, `apps/api/uv.lock`, `apps/api/app/core/config.py`, `apps/api/.env.example`, `apps/api/tests/test_config.py`

- [ ] Add a failing test for default and environment-overridden `Settings.database_url`.
- [ ] Run `cd apps/api && ./.venv/bin/pytest -q tests/test_config.py`; expect failure because the field is absent.
- [ ] Add `sqlalchemy>=2.0`, `alembic>=1.16`, the `database_url` setting, and `TICKLY_DATABASE_URL` documentation.
- [ ] Run `UV_CACHE_DIR=/tmp/tickly-uv-cache mise exec -- uv sync --project apps/api --locked`.
- [ ] Re-run the focused tests; expect all config tests to pass.

### Task 2: SQLite Engine and Session boundary

**Files:** create `apps/api/app/db/{__init__,base,session}.py`; test `apps/api/tests/test_database.py`

- [ ] Add failing tests for `foreign_keys`, `journal_mode=WAL`, positive `busy_timeout`, and rollback after an exception.
- [ ] Run the focused test; expect import failure because `app.db` is absent.
- [ ] Implement `Base`, `create_engine_for_settings(settings)`, `SessionLocal`, and generator `get_db_session()` using SQLAlchemy 2.x APIs and a SQLite connection event.
- [ ] Run the focused tests and the complete API suite; expect green.

### Task 3: Typed ORM models

**Files:** create `apps/api/app/models/{__init__,base,user,auth_session,task}.py`; test `apps/api/tests/test_models.py`

- [ ] Add failing tests for user email uniqueness, auth-session cascade, task constraints/defaults, and required task indexes.
- [ ] Run the focused test; expect missing-model failure.
- [ ] Implement `User`, `AuthSession`, and `Task` with UUID string keys, UTC timestamps, explicit constraints, relationships, and roadmap fields.
- [ ] Run model tests and the full API suite; expect green.

### Task 4: Alembic initial migration

**Files:** create `apps/api/alembic.ini`, `apps/api/alembic/env.py`, `apps/api/alembic/script.py.mako`, `apps/api/alembic/versions/0001_initial_schema.py`; test `apps/api/tests/test_migrations.py`

- [ ] Add a failing file-backed migration round-trip test: upgrade to head, inspect revision/tables, downgrade to base.
- [ ] Run the focused test; expect missing Alembic configuration/revision failure.
- [ ] Implement `env.py` with Settings URL loading and `Base.metadata` target metadata; create the reviewed initial revision matching ORM tables and indexes.
- [ ] Run the round-trip test and direct `alembic upgrade head`; expect green.

### Task 5: Documentation and final verification

**Files:** modify `README.md`

- [ ] Document explicit `alembic upgrade head`, `current`, and `downgrade base` commands.
- [ ] Run `cd apps/api && ./.venv/bin/pytest -q`.
- [ ] Run `env CI=true mise exec -- pnpm check` and inspect `git status --short`.
- [ ] Review the migration and tests for scope, then commit intentional changes.
