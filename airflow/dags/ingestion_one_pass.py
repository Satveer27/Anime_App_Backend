"""One full ingestion pass as three dependent tasks: discovery -> fetch -> clean.

Each task runs one stage of the app's CLI in the app's own virtualenv (ADR 0001), so the DAG stays
thin and every stage has its own log and retries. A retry runs the same command, which makes the
stage resume from where its last failed run stopped.

Trigger it by hand ("Trigger DAG w/ config"). Tick `fresh` only to start a new pass over an old
failed one: it adds --fresh on the first try of each task and never on a retry.
"""

from datetime import timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

APP_DIR = "/opt/app"
APP_PYTHON = "/opt/app-venv/bin/python"
# One slot (created by airflow-init): discovery and fetch call Tenrai and must never overlap,
# because the rate limiter lives in memory (ADR 0012).
TENRAI_POOL = "tenrai"


def stage_command(stage: str) -> str:
    return (
        f"cd {APP_DIR} && {APP_PYTHON} -m app.ingestion.run_scripts.one_pass --stage {stage}"
        "{% if params.fresh and ti.try_number == 1 %} --fresh{% endif %}"
    )


with DAG(
    dag_id="ingestion_one_pass",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params={"fresh": Param(False, type="boolean", description="Ignore the saved resume positions")},
    tags=["ingestion"],
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
        execution_timeout=timedelta(hours=30),
    )
    clean = BashOperator(
        task_id="clean",
        bash_command=stage_command("clean"),
        retries=2,
        retry_delay=timedelta(minutes=5),
        execution_timeout=timedelta(hours=4),
    )

    discovery >> fetch >> clean
