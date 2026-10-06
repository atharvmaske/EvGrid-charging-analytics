FROM apache/airflow:2.10.0

USER root

RUN apt-get update && \
    apt-get install -y gcc g++ && \
    apt-get clean

USER airflow

RUN pip uninstall -y dbt dbt-core dbt-oss || true

RUN pip install --no-cache-dir \
    "dbt-core==1.8.2" \
    "dbt-postgres==1.8.2" \
    "great_expectations==1.2.5" \
    pandas \
    psycopg2-binary