"""GET /analytics/occupancy: lee route_month_metrics en Athena (boto3, rol de la EC2).

- Consulta parametrizada (ExecutionParameters): el input nunca se concatena al SQL.
- Cache en memoria por (route, month) SOLO tras una consulta Athena exitosa (TTL).
- Fallos de Athena -> 503 (no afectan al resto de la API, que usa RDS).
"""
import os
import re
import threading
import time

import boto3
from botocore.config import Config
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

REGION = os.getenv("AWS_REGION", "us-east-1")
DATABASE = os.getenv("ATHENA_DATABASE", "airline_analytics")
WORKGROUP = os.getenv("ATHENA_WORKGROUP", "primary")
OUTPUT = os.getenv("ATHENA_OUTPUT", "s3://airline-system-930581488240/athena-results/")
TIMEOUT_S = float(os.getenv("ATHENA_TIMEOUT", "9"))
CACHE_TTL = float(os.getenv("ANALYTICS_CACHE_TTL", "600"))

ROUTE_RE = re.compile(r"^[A-Z]{3}-[A-Z]{3}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

SQL = ("SELECT seats_sold, seats_capacity, occupancy FROM route_month_metrics "
       "WHERE route = ? AND month = ? LIMIT 1")

_client = None
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = threading.Lock()


def _athena():
    global _client
    if _client is None:
        _client = boto3.client("athena", region_name=REGION,
                               config=Config(retries={"max_attempts": 2}, connect_timeout=3, read_timeout=5))
    return _client


class AthenaError(Exception):
    pass


def _query(route: str, month: str) -> dict | None:
    try:
        ath = _athena()
        qid = ath.start_query_execution(
            QueryString=SQL,
            QueryExecutionContext={"Database": DATABASE},
            WorkGroup=WORKGROUP,
            ResultConfiguration={"OutputLocation": OUTPUT},
            ExecutionParameters=[f"'{route}'", f"'{month}'"],  # route/month ya validados por regex
        )["QueryExecutionId"]
        deadline = time.monotonic() + TIMEOUT_S
        delay = 0.2
        while True:
            st = ath.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            state = st["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                raise AthenaError(f"Athena {state}: {st.get('StateChangeReason', '')[:200]}")
            if time.monotonic() + delay > deadline:
                try:
                    ath.stop_query_execution(QueryExecutionId=qid)
                except Exception:
                    pass
                raise AthenaError("Athena no respondio a tiempo")
            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        rows = ath.get_query_results(QueryExecutionId=qid, MaxResults=2)["ResultSet"]["Rows"]
    except AthenaError:
        raise
    except Exception as e:  # credenciales, red, tabla inexistente, throttling...
        raise AthenaError(f"{type(e).__name__}: {str(e)[:200]}")
    if len(rows) < 2:
        return None
    sold, cap, occ = [c.get("VarCharValue") for c in rows[1]["Data"]]
    return {"seats_sold": int(float(sold)), "seats_capacity": int(float(cap)), "occupancy": float(occ)}


@router.get("/analytics/occupancy")
def occupancy(route: str = Query(...), month: str = Query(...)):
    if not ROUTE_RE.fullmatch(route):
        raise HTTPException(422, "route debe tener formato AAA-BBB")
    if not MONTH_RE.fullmatch(month):
        raise HTTPException(422, "month debe tener formato YYYY-MM")
    key = (route, month)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        row = _query(route, month)
    except AthenaError as e:
        raise HTTPException(503, f"Servicio analitico (Athena) no disponible: {e}")
    if row is None:
        raise HTTPException(404, "Sin datos para esa ruta y mes")
    out = {"route": route, "month": month, **row}
    with _lock:
        _cache[key] = (time.monotonic(), out)
    return out
