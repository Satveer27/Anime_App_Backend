"""The weekly IMDb refresh (ADR 0018). For now one task: download the dataset files and archive them.

`download` runs `run_scripts.imdb_download` in the app's virtualenv. It saves title.basics,
title.episode and title.ratings to S3 under imdb/{time}/ and records an imdb_snapshot row. If the
files are unchanged since the last archived run the script exits with 99, which Airflow shows as
skipped, and the tasks after it are skipped too. The match task is added by a later issue.

It shares the one-slot `tenrai` pool (ADR 0017), so it never overlaps another pipeline task on
the small box. Trigger a run by hand any time from the UI.
"""

from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

APP_DIR = "/opt/app"
APP_PYTHON = "/opt/app-venv/bin/python"
TENRAI_POOL = "tenrai"
# The script's "files unchanged" exit code (EXIT_SKIPPED in imdb_download.py).
SKIPPED_EXIT_CODE = 99

with DAG(
    dag_id="imdb_weekly",
    schedule="0 3 * * 1",  # Monday 03:00 UTC
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["imdb"],
) as dag:
    download = BashOperator(
        task_id="download",
        bash_command=f"cd {APP_DIR} && {APP_PYTHON} -m app.ingestion.run_scripts.imdb_download",
        skip_on_exit_code=SKIPPED_EXIT_CODE,
        pool=TENRAI_POOL,
        retries=3,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=1),
    )
