from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

SILVER_SCHEDULE = None
DBT_SELECT = "stg_station_metrics"

DBT_PREAMBLE = """
set -euo pipefail
cd /opt/airflow/dbt
export DBT_PROFILES_DIR=/opt/airflow/dbt
"""

default_args = {
    "owner": "evgrid",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="evgrid_silver_pipeline",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule=SILVER_SCHEDULE,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=45),
    tags=["silver", "dbt"],
) as dag:

    run_dbt_silver_models = BashOperator(
        task_id="run_dbt_silver_models",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=20),
        bash_command=DBT_PREAMBLE
        + f"""
        echo "Running silver models..."
        dbt run --select {DBT_SELECT} --target docker
        """,
    )

    run_dbt_silver_tests = BashOperator(
        task_id="run_dbt_silver_tests",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=20),
        bash_command=DBT_PREAMBLE
        + f"""
        echo "Running silver tests..."
        dbt test --select {DBT_SELECT} --target docker
        """,
    )

    run_dbt_silver_models >> run_dbt_silver_tests