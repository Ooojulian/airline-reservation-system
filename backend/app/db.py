"""Pool de conexiones PostgreSQL (psycopg 3). Configuración por variables de entorno DB_*."""
import os

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _conninfo() -> str:
    return make_conninfo(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "airline_oltp"),
        user=os.getenv("DB_USER", "airline_admin"),
        password=os.getenv("DB_PASSWORD", ""),
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=10,
    )


# open=False: se abre en el arranque de la app (lifespan). Cada worker de uvicorn tiene su propio pool.
pool = ConnectionPool(
    conninfo=_conninfo(),
    min_size=int(os.getenv("DB_POOL_MIN", "4")),
    max_size=int(os.getenv("DB_POOL_MAX", "20")),
    timeout=float(os.getenv("DB_POOL_TIMEOUT", "60")),
    kwargs={"row_factory": dict_row},
    open=False,
)
