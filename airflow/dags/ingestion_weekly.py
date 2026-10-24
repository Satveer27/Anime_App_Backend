"""The weekly refresh of the Active tier (ADR 0004): discovery -> fetch -> clean, every Sunday.

Discovery resets the tasks of the anime that are airing or not yet aired (Tenrai's two lists plus
every anime the catalogue holds as live, so shows that just finished get a last refresh); fetch
downloads them again; clean parses only what was just fetched. Each task runs one stage of
`run_scripts.refresh --tier weekly` in the app's virtualenv, and a retry resumes where its stage
stopped.

Discovery and fetch share the one-slot `tenrai` pool (ADR 0012), so they never overlap with the
monthly refresh or the one-pass DAG. Trigger a run by hand any time from the UI.
"""

from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

APP_DIR = "/opt/app"
APP_PYTHON = "/opt/app-venv/bin/python"
TENRAI_POOL = "tenrai"


def stage_command(stage: str) -> str:
    return (
        f"cd {APP_DIR} && {APP_PYTHON} -m app.ingestion.run_scripts.refresh "
        f"--tier weekly --stage {stage}"
    )


with DAG(
    dag_id="ingestion_weekly",
    schedule="0 3 * * 0",  # Sunday 03:00 UTC
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["ingestion", "refresh"],
) as dag:
    discovery = BashOperator(
        task_id="discovery",
        bash_command=stage_command("discovery"),
        pool=TENRAI_POOL,
        retries=2,
        retry_delay=timedelta(minutes=5),
        execution_timeout=timedelta(hours=1),
    )
    fetch = BashOperator(
        task_id="fetch",
        bash_command=stage_command("fetch"),
        pool=TENRAI_POOL,
        retries=3,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=6),
    )
    clean = BashOperator(
        task_id="clean",
        bash_command=stage_command("clean"),
        retries=2,
        retry_delay=timedelta(minutes=5),
        execution_timeout=timedelta(hours=2),
    )

    discovery >> fetch >> clean
