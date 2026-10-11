# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AnimePlanet backend: a FastAPI app meant to provide anime recommendations and analysis based on a MAL profile. Currently implemented: authentication and user management only. Stack: FastAPI, async SQLAlchemy + asyncpg (Postgres), Alembic, Redis, Celery (+ beat), Resend for email, structlog. Dependencies are managed with `uv`.

The owner is building this as a job-landing portfolio project and is learning. Unless asked otherwise, explain the approach and let them implement; do not write feature logic unprompted.

## Commands

```bash
uv sync --group dev                      # install deps incl. test tools
docker compose up -d db redis            # Postgres (host port 5433) + Redis
uv run --env-file .env.local alembic upgrade head              # apply migrations (from host)
uv run --env-file .env.local uvicorn app.main:app --reload     # run API from host (docs at /docs)
docker compose up --build                # full stack: app, celery worker, celery beat

uv run pytest                            # all tests (no real DB/Redis needed)
uv run pytest tests/users/test_user_service.py::test_name   # single test

uv run --env-file .env.local alembic revision --autogenerate -m "msg"   # new migration
uv run --env-file .env.local alembic check                              # CI fails if models and migrations drift
```

CI (`.github/workflows/pipeline.yml`) runs migrations, `alembic check`, pytest, then builds the compose stack and polls `/health`.

## Configuration

`app/config.py` loads `.env.{APP_ENV}` (default `dev`, so `.env.dev`; tests use `APP_ENV=test`). All settings are required except a few defaults, so the app fails at import if the env file is incomplete. `.env.*` is gitignored.

Two env files exist for two network contexts: `.env.dev` uses Docker service hostnames (`db`, `redis`) and is what the compose containers load; `.env.local` points at `localhost` (Postgres on host port 5433) and is for commands run from the host. Always pass `--env-file .env.local` to `uv run` for app/alembic commands on the host; the exported variables override the `.env.dev` default. `pytest` doesn't need it (`tests/conftest.py` sets its own env).

## Architecture

Each feature is a package under `app/` (`users`, `security`) with the same layering: `controller.py` (routes) -> `service.py` (business logic) -> `repository.py` (SQLAlchemy queries), plus `models.py`, `schemas.py` (Pydantic), `exceptions.py`, `deps.py`, and `tasks/` (Celery). Follow this layout for new features and register the router in `app/router.py` (mounted under `/api/v1`).

- **Dependency wiring**: services and repositories are built by FastAPI dependency factories (`create_user_service` in `app/users/deps.py`, `create_*_repository` in `app/core/deps.py`). Auth guards `get_current_user` and `require_admin` live in `app/core/deps.py`.
- **Errors**: raise `AppError` subclasses (`app/exceptions.py`, per-feature `exceptions.py`); handlers registered in `app/main.py` convert them, validation errors, and unhandled exceptions to responses.
- **Auth model**: short-lived JWT access tokens; refresh tokens are rotated and stored in the DB (`RefreshToken`, keyed by `jti`) and sent as an HttpOnly cookie scoped to `/api/v1/auth`. Reusing an already-revoked refresh token revokes all of that user's sessions. Revoked access-token `jti`s are tracked in Redis (`app/security/utils/redis_util.py`), checked on every authenticated request.
- **Background work**: Celery app is `app.core.workers.celery_worker` (task modules listed in `include=`; new task modules must be added there). Beat runs a weekly expired-refresh-token cleanup. Emails (verification, password reset) are sent via Celery tasks.
- **Lifespan**: startup pings Postgres and Redis and aborts if either is unreachable.

## Testing

Tests are unit/controller tests with mocks: repositories use `create_autospec`, Redis is an `AsyncMock`, Celery `.delay` is patched, and controller tests override FastAPI dependencies (`app.dependency_overrides`). The `client` fixture deliberately does not enter the app lifespan. Shared fixtures are in `tests/conftest.py`; `tests/conftest.py` sets the env vars before `app` is imported, so keep that import order.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (Satveer27/F1_backend) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
