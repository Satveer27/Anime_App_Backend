# Airflow owns all scheduled jobs; Celery is request-triggered only

Every scheduled job (ingestion refreshes, IMDb processing, view refreshes, rollups, expired refresh-token cleanup) runs in Airflow, and Celery Beat is removed. Celery workers stay for request-triggered work only (emails, profile imports). DAGs stay thin: each job is a CLI entry point in the app package, run in a separate virtualenv so Airflow's pinned dependencies never conflict with the app's. Chosen so scheduling, retries and backfills live in one observable place, and because it is the skill the project sets out to demonstrate.

## Consequences

- The pipeline box must always run the production schedules. A laptop is only for development and the one-off Backfill.
- Moving the token cleanup replaces the single entry in `beat_schedule`.
