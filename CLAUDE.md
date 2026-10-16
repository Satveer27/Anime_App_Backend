# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AnimePlanet backend: a FastAPI app meant to provide anime recommendations and analysis based on a MAL profile. Currently implemented: authentication and user management only. Stack: FastAPI, async SQLAlchemy + asyncpg (Postgres), Alembic, Redis, Celery (request-triggered work only), Resend for email, structlog. Dependencies are managed with `uv`. The product vision, vocabulary and decisions live in `CONTEXT.md` and `docs/adr/`; read them before planning a feature.

The owner is building this as a job-landing portfolio project and is learning. Unless asked otherwise, explain the approach and let them implement; do not write feature logic unprompted.

## Commands

```bash
uv sync --group dev                      # install deps incl. test tools
docker compose up -d db redis            # Postgres (host port 5433) + Redis
uv run --env-file .env.local alembic upgrade head              # apply migrations (from host)
uv run --env-file .env.local uvicorn app.main:app --reload     # run API from host (docs at /docs)
docker compose up --build                # full stack: app, celery worker, celery beat (until the Airflow migration)

uv run pytest                            # all tests (no real DB/Redis needed)
uv run pytest tests/users/test_user_service.py::test_name   # single test

uv run ruff check --fix .                # lint (a Claude hook also runs this on every edited .py file)
uv run ruff format .                     # format

uv run --env-file .env.local alembic revision --autogenerate -m "msg"   # new migration
uv run --env-file .env.local alembic check                              # CI fails if models and migrations drift
```

CI (`.github/workflows/pipeline.yml`) runs `ruff check` and `ruff format --check`, migrations, `alembic check`, pytest, then builds the compose stack and polls `/health`.

## Configuration

`app/config.py` loads `.env.{APP_ENV}` (default `dev`, so `.env.dev`; tests use `APP_ENV=test`). All settings are required except a few defaults, so the app fails at import if the env file is incomplete. `.env.*` is gitignored.

Two env files exist for two network contexts: `.env.dev` uses Docker service hostnames (`db`, `redis`) and is what the compose containers load; `.env.local` points at `localhost` (Postgres on host port 5433) and is for commands run from the host. Always pass `--env-file .env.local` to `uv run` for app/alembic commands on the host; the exported variables override the `.env.dev` default. `pytest` doesn't need it (`tests/conftest.py` sets its own env).

## Architecture

Each feature is a package under `app/` (`users`, `security`) with the same layering: `controller.py` (routes) -> `service.py` (business logic) -> `repository.py` (SQLAlchemy queries), plus `models.py`, `schemas.py` (Pydantic), `exceptions.py`, `deps.py`, and `tasks/` (Celery). Follow this layout for new features and register the router in `app/router.py` (mounted under `/api/v1`).

- **Dependency wiring**: services and repositories are built by FastAPI dependency factories (`create_user_service` in `app/users/deps.py`, `create_*_repository` in `app/core/deps.py`). Auth guards `get_current_user` and `require_admin` live in `app/core/deps.py`.
- **Errors**: raise `AppError` subclasses (`app/exceptions.py`, per-feature `exceptions.py`); handlers registered in `app/main.py` convert them, validation errors, and unhandled exceptions to responses.
- **Auth model**: short-lived JWT access tokens; refresh tokens are rotated and stored in the DB (`RefreshToken`, keyed by `jti`) and sent as an HttpOnly cookie scoped to `/api/v1/auth`. Rotation is atomic (`UPDATE ... WHERE revoke = false`). Reusing an already-revoked refresh token revokes all of that user's sessions, unless it was revoked within `REFRESH_GRACE_SECONDS` (default 10), in which case it is a racing refresh and gets a 401 without a wipe (ADR 0013). Revoked access-token `jti`s are tracked in Redis (`app/security/utils/redis_util.py`), checked on every authenticated request.
- **Background work**: Celery app is `app.core.workers.celery_worker` (task modules listed in `include=`; new task modules must be added there). Celery handles request-triggered work only; scheduled jobs belong to Airflow (ADR 0001). The weekly expired-refresh-token cleanup is still a Beat entry in `celery_worker.py` until it is migrated, and the `celery_beat` compose service stays until then. Emails (verification, password reset) are sent via Celery tasks.
- **Lifespan**: startup pings Postgres and Redis and aborts if either is unreachable.

## Testing

Tests are unit/controller tests with mocks: repositories use `create_autospec`, Redis is an `AsyncMock`, Celery `.delay` is patched, and controller tests override FastAPI dependencies (`app.dependency_overrides`). The `client` fixture deliberately does not enter the app lifespan. Shared fixtures are in `tests/conftest.py`; `tests/conftest.py` sets the env vars before `app` is imported, so keep that import order.

## Conventions

### Logging

Use structlog with key-value fields (`log.info("refresh_token_revoked", user_id=...)`), not formatted strings. Log at boundaries:
- Adapters log external failures and retries.
- Jobs log start, periodic progress and a summary.
- Services log key business events.
- Small pure utilities (e.g. the rate limiter) don't log.

Log an exception once, where it is handled, not at every layer it passes through. Never log secrets or personal data (usernames, emails, tokens).

## Workflow rules

- Before opening a pull request (or creating a GitHub issue), show the user the proposed title and description and wait for approval. Pushing and opening are only done when the user asks.
- Before saying a task is done, run `uv run pytest` and `uv run ruff check .` and report the results honestly. Tests are mock-only, so for anything touching SQL also verify the query another way (compile it or run it against the dev database) and say so if you could not.
- Also run `uv run --env-file .env.local alembic check` (needs `docker compose up -d db`; apply `alembic upgrade head` first). It is the only check that catches model/migration drift, which the mock tests cannot. If Docker is not running, say you could not run it; CI will.
- Never let ruff (or any cleanup) remove the model imports in `alembic/env.py`. They look unused but register each table on `Base.metadata`; without them `alembic check` wrongly reports every table as removed. They carry `# noqa: F401`. After any ruff `--fix` or import cleanup, re-read `alembic/env.py`, and import every new `models.py` there.
- Record every measured number (before and after) in docs/METRICS.md.

## Production standard

Treat every feature as if it ships to production. When planning or grilling a feature:
- Check it against CONTEXT.md and docs/adr/. If it contradicts an ADR, flag it; don't silently work around it. Changing a decision means a new ADR.
- Ask about security before implementation, covering whichever of these apply:
  - AuthN/AuthZ: who can call this, and is admin-only stuff (review queue, overrides) protected
  - Input validation: Pydantic on every input, size limits, no raw user input in SQL
  - Rate limits and cost caps on anything public or LLM-backed
  - Secrets from env only; least-privilege DB roles (read-only role for text-to-SQL)
  - Prompt injection: review text and user queries are data, never instructions
  - Privacy: no usernames or personal data in logs or product_events
  - Network: RDS reachable only from the app server, SSL required, CORS locked to the frontend
- Ask about failure modes: timeouts, retries, idempotency, what the user sees when it breaks
- Every feature gets an "Out of scope / known limitations" note instead of silent gaps

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (Satveer27/Anime_App_Backend) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
