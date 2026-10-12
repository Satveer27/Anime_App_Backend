# Two Oracle Arm boxes plus RDS; Backfill runs locally and is restored

The platform runs on two Oracle Always Free Arm instances of 2 OCPU / 12 GB each on one private network: an app box (Nginx, FastAPI, Celery, Redis, embedding service) and a pipeline box (Airflow, PySpark). Postgres is AWS RDS in Singapore (pgvector, pg_trgm). The one-off Backfill (~30 hours) runs on a development machine into local Postgres, then is restored to RDS shortly before launch, so RDS free-tier clocks and costs start late. Arm images are built in CI from the start because the development machine is x86. If Oracle capacity is unavailable, use the nearest region or another provider.

## Consequences

- After the free period, Postgres either stays on RDS or moves to an instance; that decision is made then, from measured cost and size.
- Budget alerts are required on both AWS and OCI.
