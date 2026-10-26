# The pipeline runs on an Oracle VM; GitHub Actions migrates RDS and redeploys Airflow over SSH

Airflow runs on one Oracle Always Free Arm VM in London (`uk-london-1`) and talks to RDS in London (`eu-west-2`), S3 and Tenrai. A push to `main` migrates the anime database when an Alembic file changed, and recreates the Airflow containers when a file under `airflow/` changed. This corrects ADR 0005 on three points: RDS is in London and not Singapore, the pipeline box began as a single VM, and the backfill ran directly against RDS from a laptop (15 h 26 min, `docs/METRICS.md` section 5) instead of locally with a later restore.

## Decisions

- **One pipeline VM** (Ubuntu 24.04, Arm, `VM.Standard.A1.Flex`, created at 1 OCPU / 6 GB, to be raised to the 2 OCPU / 12 GB of ADR 0005). Airflow uses `LocalExecutor`. The app box of ADR 0005 is still to come and joins the same VCN.
- **The image holds everything it runs** (`airflow/Dockerfile.prod`): the app's virtualenv, `app/`, `alembic/` and the DAGs. No repo is mounted in production, so a deploy is one versioned image. Development keeps its mount (`docker-compose.yml`).
- **One lane for the whole pipeline.** The `tenrai` pool has one slot and holds discovery, fetch and clean of every DAG. Discovery and fetch need it for the in-memory rate limiter (ADR 0012). Clean needs it so that two cleans never write the catalogue at once and none runs while a fetch is still adding rows (two ran together in the first refresh, `docs/METRICS.md` section 5).
- **CI deploys only on a push to `main`**, never for a pull request or a manual run, and only after `startup-and-test` and `dockerfile-build` succeeded.
  - **`migrate`** runs when a file under `alembic/` (or `alembic.ini`) changed.
  - **`deploy`** runs when a file under `airflow/` changed.
  - A change under `app/` does **not** redeploy Airflow, to save runner minutes. After a fix there, redeploy by hand: `git pull --ff-only && bash airflow/deploy.sh` on the server.
  - `migrate` and `deploy` use separate concurrency groups: GitHub keeps one pending job per group, so a shared group would let a queued deploy cancel a queued migration.
- **Both run on the VM, over SSH, not on the runner.** The RDS security group accepts only known addresses and GitHub runners have changing ones. The job connects with a dedicated deploy key, with `BatchMode` and `StrictHostKeyChecking=yes` against a pinned host key (`ORACLE_KNOWN_HOSTS`). Secrets: `ORACLE_HOST`, `ORACLE_USER`, `ORACLE_SSH_KEY`, `ORACLE_KNOWN_HOSTS`. The workflow token is read-only.
- **`airflow/migrate.sh`** builds the image (cached, never restarts anything) and runs `alembic upgrade head` then `alembic check` in it. **`airflow/deploy.sh`** refuses to run while a DAG run is active, and if the database holding that answer cannot be read it also stops (never fails open), then builds, recreates the containers and waits until Airflow is healthy. A failure before the recreate leaves the running Airflow untouched.
- **Network exposure:** the VM accepts SSH only (key login), the Airflow UI is published on `127.0.0.1` and reached through an SSH tunnel, and every secret in `docker-compose.prod.yml` is required (startup fails without it). Secrets live in the server's `airflow/.env` (mode 600) and in GitHub secrets, never in the repository or the image (`.dockerignore`).
- **Failed deploy output** is limited to the container list: container logs can hold connection details and the CI log may be public.

## Considered and rejected

- **Migrating from the GitHub runner.** It would need RDS open to GitHub's address ranges.
- **Migrating inside an Airflow task.** It would tie the schema to the scheduler's health and run migrations under a running DAG.
- **Redeploying on every push.** It restarts a healthy Airflow for changes that do not touch it, and costs runner minutes while the VM builds.
- **Automatic redeploy on `app/` changes.** Considered, then dropped by the owner for runner minutes; the cost is a pipeline that keeps running old code until someone redeploys.

## Consequences

- **A migration must work with the old code too,** because it runs before the containers are replaced: add columns and tables, do not drop or rename in the same deploy.
- **A failed migration is not retried by the next push** (no Alembic file will have changed). Fix it and use "Re-run failed jobs".
- **A deploy refuses while a weekly or monthly run is active** and fails visibly; re-run it when the run is over. A deploy never kills a fetch.
- **Scheduled DAGs run only while the VM and its containers are up.** The weekly refresh on Sunday 03:00 UTC did not run when the laptop was off; the VM removes that dependency.
- **Oracle may reclaim an idle Always Free instance.** A box that works a few hours a week is a candidate; upgrading the account to Pay As You Go (free within the free limits) is the usual defence. Not verified.
- **Database connections use `ssl=require`:** encrypted, but the certificate is not verified. Full verification needs the RDS CA bundle and an SSL context in the engine (known limitation).

## Revisit if

- A second source (IMDb, AniList) needs its own pool or its own box.
- The runner minutes stop being a concern: then redeploy on `app/` changes too.
- A migration ever has to drop or rename: use an expand-then-contract sequence over two deploys.
