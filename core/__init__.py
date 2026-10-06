"""Shared business logic, imported directly by both the FastAPI live API and
the batch scripts Airflow runs (scripts/sync_bronze.py). Neither one talks to
the other over HTTP any more -- see docs/architecture.md.
"""
