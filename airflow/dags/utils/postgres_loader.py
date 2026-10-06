from airflow.providers.postgres.hooks.postgres import PostgresHook
from psycopg2.extras import execute_values
import pandas as pd
import json


class PostgresLoader:
    def __init__(self, postgres_conn_id="evgrid_postgres"):
        self.postgres_conn_id = postgres_conn_id

    def get_table_columns(self, cursor, schema, table):
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=%s
              AND table_name=%s
            ORDER BY ordinal_position;
            """,
            (schema, table),
        )
        return [r[0] for r in cursor.fetchall()]

    def upsert(self, df, table_name, primary_key, update_where=None):
        if df.empty:
            print(f"[WARNING] Empty dataframe for {table_name}")
            return

        hook = PostgresHook(postgres_conn_id=self.postgres_conn_id)
        conn = hook.get_conn()

        print("=" * 70)
        print("CONNECTED DATABASE :", conn.info.dbname)
        print("CONNECTED USER     :", conn.info.user)
        print("TARGET TABLE       :", table_name)
        print("=" * 70)

        cursor = conn.cursor()

        schema, table = table_name.split(".")
        valid_columns = self.get_table_columns(cursor, schema, table)

        print("TABLE COLUMNS:", valid_columns)

        df = df.copy()

        extra = [c for c in df.columns if c not in valid_columns]
        if extra:
            print("[INFO] Dropping extra columns:", extra)
            df.drop(columns=extra, inplace=True)

        if "ingestion_timestamp" in valid_columns:
            df["ingestion_timestamp"] = pd.Timestamp.utcnow()

        if "ingested_at" in valid_columns:
            df["ingested_at"] = pd.Timestamp.utcnow()

        for c in df.columns:
            df[c] = df[c].apply(
                lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x
            )

        print("DATAFRAME SHAPE:", df.shape)
        print(df.head(3))

        cols = list(df.columns)
        insert_cols = ",".join(cols)
        update_cols = ",".join(
            f"{c}=EXCLUDED.{c}" for c in cols if c != primary_key
        )

        where_clause = f"\n        WHERE {update_where}" if update_where else ""

        sql = f"""
        INSERT INTO {table_name} AS t ({insert_cols})
        VALUES %s
        ON CONFLICT ({primary_key})
        DO UPDATE SET {update_cols}{where_clause}
        """

        values = [tuple(row) for row in df.to_numpy()]
        execute_values(cursor, sql, values, page_size=1000)

        conn.commit()

        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        print("=" * 70)
        print("UPSERT COMPLETE :", table_name)
        print("ROWS PROCESSED  :", len(df))
        print("TABLE ROW COUNT :", count)
        print("=" * 70)