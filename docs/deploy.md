# Deploying the backend (runbook)

Issue #62. A push to `main` that changes the backend is deployed to the Oracle VM by CI. This page
is the one-time setup, the day-to-day, and what to do when something is wrong.

## What runs on the VM

One `docker compose` stack (`docker-compose.prod.yml`), next to the Airflow stack that already runs there:

| Container | What it is |
|---|---|
| `app` | The FastAPI API. Listens on port 8000 **inside** the VM only; nothing outside can reach that port. |
| `celery_worker`, `celery_beat` | Background jobs (emails) and the weekly token cleanup. Exactly one beat. |
| `redis` | Tokens, the Celery queue and the response cache. Capped at 256 MB, never evicts, saved to disk (AOF). |
| `caddy` | The only thing open to the internet (ports 80 and 443). Serves HTTPS for your DuckDNS name and passes requests to `app`. |

**What Caddy is:** the front door. Browsers talk HTTPS to Caddy on port 443. Caddy holds the certificate
(it gets and renews it for free by itself) and passes each request on to the app over plain HTTP inside the
VM. Without it a frontend on HTTPS could not call the API, and the Secure refresh cookie would not work.
Port 80 is used to prove to the certificate authority that you own the name, and to redirect `http://` to `https://`.

The database is **not** here: it is RDS (`DB_URL`). Migrations are not here either: the existing
`migrate` job runs them when an Alembic file changes.

## How a deploy happens

1. You merge a pull request to `main`.
2. CI runs the tests and the image build, as before.
3. If something the backend is built from changed (`app/`, `Dockerfile`, `pyproject.toml`, `uv.lock`,
   `docker-compose.prod.yml`, `deploy/`), the `deploy-backend` job runs:
   1. writes `.env.prod` on the VM from GitHub secrets and variables (over SSH, never in a log);
   2. SSHes in, pulls `main` and runs `deploy/deploy.sh`: builds the image on the VM, replaces the
      containers, waits until they are healthy, and **puts the previous image back if they are not**;
   3. checks from the runner that `https://<API_HOSTNAME>/api/v1/health` answers, that `/metrics` is **not**
      reachable from outside, and that CORS allows only your frontend.
4. Changes to `airflow/` or to Alembic files are handled by the existing `deploy` and `migrate` jobs.

## One-time setup

### 1. Oracle Cloud: open ports 80 and 443
The VM today accepts SSH only. Caddy needs two more ports. (You do not need to reserve the IP: the VM's
current public IP, labelled "Ephemeral" in the console, stays with the VM. Reserve one only if you ever
recreate the VM; then the RDS security group, `ORACLE_HOST`, `ORACLE_KNOWN_HOSTS` and DuckDNS must change too.)

- **In Oracle's network:** Networking, Virtual cloud networks, your VCN, your subnet, its Security List,
  Add Ingress Rules: Source Type CIDR, Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port Range `80`.
  Add a second rule the same way with `443`.
- **In the VM's own firewall** (Oracle's Ubuntu image blocks them). SSH in and run:
  ```
  sudo iptables -L INPUT --line-numbers -n      # find the line number of the REJECT rule (often 5 or 6)
  sudo iptables -I INPUT <that number> -m state --state NEW -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT <that number> -m state --state NEW -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save                # if not found: sudo apt install iptables-persistent
  ```
  The rules must be before the REJECT line, or they do nothing.
- **RDS**: its security group must already allow this VM (Airflow uses the same one).

### 2. A free name: DuckDNS
Sign in at https://www.duckdns.org (GitHub login), create a subdomain, for example `animeplanet-api`, and set
its IP to the VM's public IP (the same address as `ORACLE_HOST`). Your API address is then
`https://animeplanet-api.duckdns.org`. Caddy gets the certificate for that name on the first request.

### 3. GitHub: secrets and variables
Repository, Settings, Secrets and variables, Actions.

**Secrets** (Secrets tab, "New repository secret"):

| Name | What to put |
|---|---|
| `DB_URL` | New. The RDS URL, the same as `DB_URL` in your `.env.rds`: `postgresql+asyncpg://USER:PASSWORD@HOST:5432/postgres?ssl=require` |
| `REDIS_PASSWORD` | New. Anything random, 16 or more letters, digits, `-` or `_` (it goes into a URL): `openssl rand -hex 16` |
| `JWT_SECRET` | Already there (the test jobs use the same one). It must be 32 or more characters; if yours is shorter the deploy stops and says so. |
| `RESEND_API_KEY` | Already there (the same one the test jobs use). |

Changing `JWT_SECRET` later signs everyone out.

**Variables** (Variables tab, "New repository variable"). These are not secret, they are web addresses.
Variables are shown in plain text, which makes errors easier to read (a secret would be masked, so the
address would show as `***` in the logs). If you prefer, add them as secrets with the same names: the
workflow accepts either.

| Name | What to put |
|---|---|
| `API_HOSTNAME` | `animeplanet-api.duckdns.org` (no `https://`) |
| `FRONTEND_URL` | Your frontend's address. It is what CORS allows, and the base of the links in emails (verify email, reset password). While you build the frontend locally: `http://localhost:3000`. When it is on Vercel: `https://your-app.vercel.app`. |
| `CORS_EXTRA_ORIGINS` | Optional. More addresses allowed to call the API, comma-separated. Use it after Vercel to keep your local frontend working: `http://localhost:3000`. Empty or `none` means no extras. A wildcard (`*`) is refused. |

**Already there for the Airflow deploy and reused:** `ORACLE_HOST`, `ORACLE_USER`, `ORACLE_SSH_KEY`,
`ORACLE_KNOWN_HOSTS`. Nothing to add for these.

The values are checked before anything is sent: an empty secret, a database URL without `ssl=require`,
a short password and similar fail the job with the name of the problem value (never the value).

### 4. First deploy
Merge to `main` (or re-run the job). The first run builds the image on the VM (a few minutes) and waits up
to five minutes for the certificate. If DuckDNS or the open ports are not ready yet, the last step fails
with "did not answer": fix that and use "Re-run failed jobs".

## Calling the deployed API from your local frontend
A frontend on `http://localhost:3000` (or on Vercel) and the API on `*.duckdns.org` are different sites. Two
things make that work, and both are set by `.env.prod` in production:
- **CORS:** a browser only lets a page read the API's answers if the API names that page's address. The API
  allows `FRONTEND_URL` and whatever is in `CORS_EXTRA_ORIGINS`, nothing else. While the frontend runs on your
  laptop, set `FRONTEND_URL` to `http://localhost:3000` (it is an address, not your IP). Later, set it to the
  Vercel address and put `http://localhost:3000` in `CORS_EXTRA_ORIGINS`.
- **The login cookie:** it is set with `SameSite=None; Secure` (`COOKIE_SAMESITE=none`, `HTTP_SECURE=true`), so
  the browser keeps it on calls from another site. Locally the default `COOKIE_SAMESITE=lax` stays, because a
  local frontend and a local backend are the same site.

In the frontend, send `credentials: "include"` (fetch) or `withCredentials: true` (axios) on the calls that use
the cookie (login, refresh, logout).

Two limits of a cookie sent across sites: Safari blocks it by default, so login does not stay on Safari; and
the refresh and logout endpoints can be triggered from another site (they cannot read the response, and
access tokens are short-lived). If that matters, have the frontend host forward `/api/*` to the backend
(a Vercel rewrite), so the browser sees one site; then `SameSite=lax` works again.

## Day to day

| I want to... | Do this |
|---|---|
| Deploy | Merge to `main`. Nothing else. |
| Read the logs | On the VM, in `~/Anime_App_Backend`: `docker compose --env-file .env.prod -f docker-compose.prod.yml logs --tail 100 app` (also `celery_worker`, `caddy`). They are JSON, one event per line. |
| See what is running | `docker compose --env-file .env.prod -f docker-compose.prod.yml ps` |
| Roll back | `docker image ls anime-backend` shows the tags (commit ids, newest first). Then `bash deploy/deploy.sh <tag>`. |
| Change a secret | Change it in GitHub (Settings, Secrets and variables, Actions), then re-run the `deploy-backend` job. |
| See the app's numbers | `/metrics` is not public. On the VM: `docker compose --env-file .env.prod -f docker-compose.prod.yml exec app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode())"` (request counts, latency, cache hits and misses). |

## Known limits
- A deploy restarts the API for a few seconds.
- Nothing rate-limits the public endpoints yet.
- There is no dashboard or alerting; the app exposes `/metrics`, so Prometheus and Grafana can be added later.
- Airflow keeps its own server-side `airflow/.env` (ADR 0017) and its own deploy job.
- If `.env.prod` is changed by hand on the VM, the next deploy overwrites it.
