from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

DEXEC = "docker exec evgrid-fastapi"

DBT_PREAMBLE = """
set -euo pipefail
cd /opt/airflow/dbt
export DBT_PROFILES_DIR=/opt/airflow/dbt
"""

default_args = {
    "owner": "evgrid",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
}

with DAG(
    dag_id="evgrid_germany_smard_daily",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule="30 14 * * *",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=20),
    tags=["evgrid", "germany", "bronze", "smard"],
) as smard_dag:

    extract_smard = BashOperator(
        task_id="extract_smard_day_ahead",
        bash_command=f"{DEXEC} python -m ingestion.extract_smard --days-back 2",
    )

    dbt_freshness_price = BashOperator(
        task_id="dbt_source_freshness_de_price",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=10),
        bash_command=DBT_PREAMBLE + "dbt source freshness --target docker --select source:bronze.day_ahead_prices_de",
    )

    dbt_build_price = BashOperator(
        task_id="dbt_build_de_price_models",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=15),
        bash_command=DBT_PREAMBLE + "dbt build --select day_ahead_prices_de mart_revenue_hourly+ --target docker",
    )

    extract_smard >> dbt_freshness_price >> dbt_build_price


with DAG(
    dag_id="evgrid_germany_bnetza_weekly",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule="0 3 * * 1",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=1),
    tags=["evgrid", "germany", "bronze", "bnetza"],
) as bnetza_dag:

    extract_bnetza = BashOperator(
        task_id="extract_bnetza_register",
        bash_command=f"{DEXEC} python -m ingestion.extract_bnetza",
    )

    match_bnetza = BashOperator(
        task_id="match_bnetza_to_stations",
        bash_command=f"{DEXEC} python -m ingestion.match_bnetza_stations",
    )

    dbt_build_power = BashOperator(
        task_id="dbt_build_de_power_models",
        pool="dbt",
        do_xcom_push=False,
        execution_timeout=timedelta(minutes=15),
        bash_command=DBT_PREAMBLE + "dbt build --select station_power_de mart_energy_hourly+ --target docker",
    )

    extract_bnetza >> match_bnetza >> dbt_build_power