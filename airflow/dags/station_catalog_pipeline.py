from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

default_args = {
    "owner": "evgrid",
    "retries": 1,
    "retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(hours=2),
}

DEXEC = "docker exec evgrid-fastapi"

with DAG(
    dag_id="evgrid_station_catalog_pipeline",
    default_args=default_args,
    start_date=datetime(2026, 9, 1),
    schedule="@weekly",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=3),
    tags=["evgrid", "bronze", "catalog", "weekly"],
) as dag:

    extract_ocm_all = BashOperator(
        task_id="extract_ocm_all",
        bash_command=f"{DEXEC} python -m ingestion.extract_ocm",
    )

    generate_stations_all = BashOperator(
        task_id="generate_charging_stations_all",
        bash_command=f"{DEXEC} python -m ingestion.generate_charging_stations --all",
    )

    extract_tomtom_all = BashOperator(
        task_id="extract_tomtom_all_live_cities",
        bash_command=f"{DEXEC} python -m ingestion.extract_tomtom",
    )

    merge_tomtom_all = BashOperator(
        task_id="merge_tomtom_stations_all",
        bash_command=f"{DEXEC} python -m ingestion.merge_tomtom_stations --all",
    )

    refresh_availability = BashOperator(
        task_id="generate_station_availability",
        bash_command=f"{DEXEC} python -m ingestion.generate_station_availability",
    )

    refresh_sessions = BashOperator(
        task_id="generate_synthetic_sessions",
        bash_command=f"{DEXEC} python -m ingestion.generate_synthetic_sessions",
    )

    trigger_ingest = TriggerDagRunOperator(
        task_id="trigger_ingest_pipeline",
        trigger_dag_id="evgrid_ingest_pipeline",
        wait_for_completion=False,
    )

    (
        extract_ocm_all
        >> generate_stations_all
        >> extract_tomtom_all
        >> merge_tomtom_all 
        >> refresh_availability
        >> refresh_sessions
        >> trigger_ingest
    )