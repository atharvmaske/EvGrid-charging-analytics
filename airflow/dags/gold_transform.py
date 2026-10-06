from datetime import datetime, timedelta, timezone

import pandas as pd
from airflow import DAG
from airflow.exceptions import AirflowSkipException
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from sqlalchemy import inspect

CONN_ID = "evgrid_postgres"
SILVER_TABLE = "silver.stg_station_metrics"
OCCUPANCY_TO_PERCENT = True
GOLD_SCHEDULE = None

REQUIRED_COLUMNS = {
    "station_name",
    "city",
    "connectors",
    "available_connectors",
    "occupied_connectors",
    "occupancy_rate",
}


def get_engine():
    return PostgresHook(postgres_conn_id=CONN_ID).get_sqlalchemy_engine()


def create_gold_tables():
    queries = [
        """
        CREATE TABLE IF NOT EXISTS gold_city_summary (
            city TEXT PRIMARY KEY,
            total_stations INTEGER,
            total_connectors INTEGER,
            available_connectors INTEGER,
            occupied_connectors INTEGER,
            avg_occupancy DOUBLE PRECISION,
            updated_at TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS gold_top_stations (
            station_name TEXT,
            city TEXT,
            occupancy_rate DOUBLE PRECISION,
            available_connectors INTEGER,
            connectors INTEGER,
            updated_at TIMESTAMP
        );
        """
    ]

    engine = get_engine()
    with engine.begin() as conn:
        for query in queries:
            conn.exec_driver_sql(query)


def transform_to_gold():
    engine = get_engine()

    schema, _, table = SILVER_TABLE.rpartition(".")
    if not inspect(engine).has_table(table, schema=schema or None):
        raise RuntimeError(
            f"Source table '{SILVER_TABLE}' does not exist. "
            "Point SILVER_TABLE at the table your silver layer builds."
        )

    df = pd.read_sql(f"SELECT * FROM {SILVER_TABLE}", engine)

    if df.empty:
        raise AirflowSkipException(f"{SILVER_TABLE} is empty; gold left unchanged.")

    if "connectors" not in df.columns and "total_connectors" in df.columns:
        df = df.rename(columns={"total_connectors": "connectors"})

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"{SILVER_TABLE} is missing columns: {sorted(missing)}")

    if OCCUPANCY_TO_PERCENT:
        df["occupancy_rate"] = (df["occupancy_rate"] * 100).round(2)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    city_summary = (
        df.groupby("city")
        .agg(
            total_stations=("station_name", "count"),
            total_connectors=("connectors", "sum"),
            available_connectors=("available_connectors", "sum"),
            occupied_connectors=("occupied_connectors", "sum"),
            avg_occupancy=("occupancy_rate", "mean"),
        )
        .reset_index()
    )

    city_summary["avg_occupancy"] = city_summary["avg_occupancy"].round(2)
    city_summary["updated_at"] = now

    top_stations = (
        df.sort_values(
            ["occupancy_rate", "connectors", "station_name"],
            ascending=[False, False, True],
        )
        .head(20)
        .copy()
    )

    top_stations["updated_at"] = now

    top_stations = top_stations[
        [
            "station_name",
            "city",
            "occupancy_rate",
            "available_connectors",
            "connectors",
            "updated_at",
        ]
    ]

    with engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE TABLE gold_city_summary")
        city_summary.to_sql(
            "gold_city_summary", conn, if_exists="append", index=False
        )

        conn.exec_driver_sql("TRUNCATE TABLE gold_top_stations")
        top_stations.to_sql(
            "gold_top_stations", conn, if_exists="append", index=False
        )

    print(f"Created {len(city_summary)} city summaries.")
    print(f"Created {len(top_stations)} top station records.")


default_args = {
    "owner": "evgrid",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="evgrid_gold_pipeline",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule=GOLD_SCHEDULE,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    tags=["gold", "analytics"],
) as dag:

    create_tables = PythonOperator(
        task_id="create_gold_tables",
        python_callable=create_gold_tables,
    )

    transform = PythonOperator(
        task_id="transform_silver_to_gold",
        python_callable=transform_to_gold,
    )

    create_tables >> transform