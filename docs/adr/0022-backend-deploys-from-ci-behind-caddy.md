# The backend deploys from CI to the Oracle VM, behind Caddy

**Status: draft.** Not accepted until the owner has reviewed it. Extends ADR 0017 (which deploys only Airflow); changes nothing in it.

## Decision

- A push to `main` that changes the backend (`app/`, the Dockerfile, the dependencies, `docker-compose.prod.yml`, `deploy/`) deploys the API, the Celery worker and beat, and Redis to the same Oracle VM as Airflow, against RDS. CI does it over SSH with the deploy key and pinned host key of ADR 0017, in its own `deploy-backend` job with its own concurrency group.
- **The image is built on the VM** (it is Arm and the runner is x86), tagged with the commit id. A deploy that does not come up healthy puts the previous image back; `deploy/deploy.sh <tag>` rolls back by hand. The last five images are kept.
- **Migrations stay with the existing `migrate` job.** The Dockerfile's default command also migrates, so the production compose file overrides it: no container migrates on start. `deploy-backend` runs after a skipped or successful `migrate` and not after a failed one.
- **Secrets come from GitHub repository secrets and variables** and are written to `.env.prod` on the VM at each deploy, over SSH stdin, after `deploy/render-env.sh` has checked them (non-empty, SSL on the database URL, minimum lengths, no characters an env file cannot hold). The JWT secret and the Resend key are the same ones the test jobs use (the owner chose not to keep separate production values). Airflow keeps its hand-written server-side `airflow/.env` (ADR 0017); the two are not merged.
- **Public access is Caddy** on ports 80 and 443 with a free DuckDNS name: it gets and renews the HTTPS certificate by itself and passes requests to the app, which is published nowhere else. The refresh cookie is `Secure` and a browser frontend on HTTPS cannot call a plain HTTP API, so HTTPS is required, not optional. `/metrics` is answered with 404 on the public name. Caddy writes no access log, and the app runs with `--no-access-log`, because a URL holds what people search for.
- **CORS** allows `FRONTEND_URL` plus the optional `CORS_EXTRA_ORIGINS` (never a wildcard), with credentials (the refresh token is an HttpOnly cookie). While the frontend is built locally, `FRONTEND_URL` can be `http://localhost:3000`.
- **The refresh cookie is `SameSite=None; Secure` in production** (`COOKIE_SAMESITE=none`), because the frontend (localhost, then Vercel) and the API (`duckdns.org`) are different sites and a `Lax` cookie is ignored across sites. Development keeps `lax`. The cost: Safari blocks cross-site cookies, and the refresh and logout endpoints can be triggered from another site (CORS stops it reading the result, access tokens are short-lived). Forwarding `/api/*` through the frontend host would restore `lax`.
- **Logging:** structlog prints JSON in production (`LOG_JSON=true`), one event per line; containers rotate their logs (10 MB x 5).
- **Metrics:** the app exposes `/metrics` (request counts and latency by route template, never the raw URL, plus the cache counters). Nothing collects them yet.
- **Redis** in production: password, `maxmemory 256mb` with `noeviction` (as the cache requires), AOF persistence (the revoked-token list and the queue survive a restart), no published port.

## Considered and rejected

- **Prometheus, Grafana and Alertmanager on the VM.** Built and tested, then removed by the owner as more than a portfolio project needs: four more containers, a monitoring config folder and two more secrets. The `/metrics` page stays, so they can be added later without touching the app.
- **Nginx.** Caddy does the same job here with a three-line config and automatic certificates. Rate limiting is therefore not included (below).
- **A Cloudflare Tunnel.** No open ports, but it needs a domain on Cloudflare.
- **Plain HTTP on another port.** Browsers block it from an HTTPS frontend, and the Secure cookie would not be sent.
- **Building the image in CI and pushing it to a registry.** More moving parts (buildx, QEMU or an Arm runner, a registry login); the VM builds in a few minutes and Airflow already does this.
- **Keeping the secrets in a hand-written file on the server** (as Airflow does). Rotating a secret would mean editing a file by hand on the VM.

## Consequences

- A deploy restarts the API (a few seconds of downtime); Celery gets 60 s to finish a running task.
- Public endpoints are not rate limited until something in front of Caddy or a Caddy module adds it.
- Nothing alerts when the app or the VM is down; the deploy check and your own use are the only signals.
- Two paths for secrets exist (GitHub environment for the backend, server file for Airflow) until Airflow is moved.
- The Caddyfile is bind-mounted, which a recreate does not notice: `deploy.sh` reloads it after every deploy.

## Revisit if

- Traffic needs rate limiting or more than one API process (then `/metrics` needs multiprocess mode).
- You want dashboards or alerts: add Prometheus, Grafana and Alertmanager (the `/metrics` page is ready).
