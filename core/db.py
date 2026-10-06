import logging
import os

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("evgrid.db")


DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}


def resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"


DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST"),   
    "port": os.getenv("POSTGRES_PORT"),
    "dbname": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}

def _required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set (see .env.example)")
    return value


def database_url():
    from sqlalchemy.engine import URL
    return URL.create(
        "postgresql+psycopg2",
        username=_required("POSTGRES_USER"),
        password=_required("POSTGRES_PASSWORD"),
        host=resolve_host(os.getenv("POSTGRES_HOST", "postgres")),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "evgrid"),
    )


def get_engine():
    from sqlalchemy import create_engine
    url = database_url()
    logger.info("Connecting to %s:%s/%s", url.host, url.port, url.database)
    return create_engine(url, future=True, pool_pre_ping=True)


def get_connection():
    """psycopg2 connection, for scripts that use raw cursors (matching the
    rest of ingestion/) rather than SQLAlchemy."""
    import psycopg2
    url = database_url()
    return psycopg2.connect(
        host=url.host, port=url.port, dbname=url.database,
        user=url.username, password=url.password,
    )


_engine = None


def __getattr__(name):
    global _engine
    if name == "engine":
        if _engine is None:
            _engine = get_engine()
        return _engine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
