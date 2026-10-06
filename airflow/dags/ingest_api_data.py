
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

DEXEC = "docker exec evgrid-fastapi"

DBT_PREAMBLE = """
set -euo pipefail
cd /opt/airflow/dbt
export DBT_PROFILES_DIR=/opt/airflow/dbt
"""

default_args = {
    "owner": "evgrid",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=15),
}

with DAG(
    dag_id="evgrid_ingest_pipeline",
    default_args=default_args,
    start_date=datetime(2026, 9, 1),
    schedule="@hourly",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=1),
    tags=[
        "evgrid",
        "bronze",
        "silver",
        "gold",
        "dbt",
        "great_expectations",
    ],
) as dag:

    sync_availability = BashOperator(
        task_id="sync_availability_all_cities",
        bash_command=f"{DEXEC} python -m scripts.sync_bronze --dataset availability --all-cities",
    )

    sync_weather = BashOperator(
        task_id="sync_weather_all_cities",
        bash_command=f"{DEXEC} python -m scripts.sync_bronze --dataset weather --all-cities",
    )

    great_expectations_task = BashOperator(
        task_id="run_great_expectations",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=20),
        bash_command="""
        set -euo pipefail
        echo "Running Great Expectations..."
        python /opt/airflow/quality/checkpoint.py
        """,
    )

    dbt_freshness_task = BashOperator(
        task_id="dbt_source_freshness",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=10),
        bash_command=DBT_PREAMBLE
        + """
        echo "Checking source freshness..."
        dbt source freshness --target docker --select source:bronze.weather_raw source:bronze.weather source:bronze.availability
        """,
    )

    dbt_run_task = BashOperator(
        task_id="dbt_run",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=30),
        bash_command=DBT_PREAMBLE
        + """
        echo "Running dbt models..."
        dbt run --target docker
        """,
    )

    dbt_test_task = BashOperator(
        task_id="dbt_test",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=10),
        bash_command=DBT_PREAMBLE
        + """
        echo "Running dbt tests..."
        dbt test --target docker
        """,
    )

    trigger_gold = TriggerDagRunOperator(
        task_id="trigger_gold_pipeline",
        trigger_dag_id="evgrid_gold_pipeline",
        wait_for_completion=False,
    )

    bronze_tasks = [sync_availability, sync_weather]

    (
        bronze_tasks
        >> great_expectations_task
        >> dbt_freshness_task
        >> dbt_run_task
        >> dbt_test_task
        >> trigger_gold
    )
