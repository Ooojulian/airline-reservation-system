"""Parcial 3: reconstrucción del esquema estrella con Athena (CTAS), disparada por POST /admin/refresh-star.

Cada refresh reconstruye todas las tablas desde la zona de aterrizaje (raw/events/): DROP TABLE, borrar su
carpeta en S3 y CTAS. Así es idempotente (correrlo dos veces da lo mismo) y los eventos tardíos o
desordenados quedan en su fecha real (date_key sale de event_ts). La deduplicación es por event_id.
"""
import logging
import os
import threading
import time
import uuid

import boto3
from fastapi import APIRouter

log = logging.getLogger("star")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
BUCKET = os.getenv("STAR_BUCKET", "")
DB = os.getenv("ATHENA_DATABASE", "airline_star")
WORKGROUP = os.getenv("ATHENA_WORKGROUP", "airline-p3")
RAW = f"s3://{BUCKET}/raw/events/"
STAR = f"s3://{BUCKET}/star/"
REF = f"s3://{BUCKET}/ref/"

# Eventos crudos limpios: una fila por event_id (la primera copia) y event_ts válido.
EVENTS = """
WITH e AS (
  SELECT *, date_parse(event_ts, '%Y-%m-%dT%H:%i:%sZ') AS ts,
         row_number() OVER (PARTITION BY event_id ORDER BY event_ts) AS rn
  FROM raw_events
  WHERE event_id IS NOT NULL AND try(date_parse(event_ts, '%Y-%m-%dT%H:%i:%sZ')) IS NOT NULL
), ev AS (SELECT * FROM e WHERE rn = 1)
"""

SETUP = [
    f"""CREATE EXTERNAL TABLE IF NOT EXISTS raw_events (
      event_id string, event_type string, event_ts string, schema_version int,
      session_id string, origin string, destination string, travel_date string, results_count int,
      reservation_id string, flight_id string, route string, flight_date string, fare_class string,
      price_cents bigint)
    ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe' WITH SERDEPROPERTIES ('ignore.malformed.json'='true')
    LOCATION '{RAW}'""",
    f"""CREATE EXTERNAL TABLE IF NOT EXISTS ref_routes (route string, origin string, destination string, region string)
    ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde' LOCATION '{REF}routes/'
    TBLPROPERTIES ('skip.header.line.count'='1')""",
    f"""CREATE EXTERNAL TABLE IF NOT EXISTS ref_route_history (route string, route_category string, effective_date string)
    ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde' LOCATION '{REF}route_history/'
    TBLPROPERTIES ('skip.header.line.count'='1')""",
    f"""CREATE EXTERNAL TABLE IF NOT EXISTS ref_holidays (holiday_date string, holiday_name string)
    ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde' LOCATION '{REF}holidays/'
    TBLPROPERTIES ('skip.header.line.count'='1')""",
]

# (tabla, SELECT, partición). El orden importa: las dimensiones antes que los hechos.
TABLES = [
    ("dim_date", """
WITH d AS (SELECT dt FROM UNNEST(sequence(DATE '2023-01-01', DATE '2027-12-31', INTERVAL '1' DAY)) AS t(dt)),
h AS (SELECT CAST(holiday_date AS date) AS hd, max(holiday_name) AS holiday_name FROM ref_holidays GROUP BY 1)
SELECT CAST(date_format(d.dt, '%Y%m%d') AS integer) AS date_key,
       d.dt AS full_date,
       year(d.dt) AS year, month(d.dt) AS month, quarter(d.dt) AS quarter,
       day_of_week(d.dt) AS weekday,                         -- 1 = lunes ... 7 = domingo
       date_format(d.dt, '%W') AS weekday_name,
       date_format(d.dt, '%M') AS month_name,
       date_format(d.dt, '%Y-%m') AS year_month,
       day_of_week(d.dt) >= 6 AS is_weekend,
       h0.hd IS NOT NULL AS is_holiday,
       coalesce(h0.holiday_name, '') AS holiday_name,
       CASE day_of_week(d.dt)
         WHEN 1 THEN h0.hd IS NOT NULL                       -- lunes festivo
         WHEN 5 THEN h0.hd IS NOT NULL                       -- viernes festivo
         WHEN 6 THEN hm1.hd IS NOT NULL OR hp2.hd IS NOT NULL -- sábado: viernes antes o lunes después
         WHEN 7 THEN hm2.hd IS NOT NULL OR hp1.hd IS NOT NULL -- domingo
         ELSE false END AS is_long_weekend
FROM d
LEFT JOIN h h0  ON h0.hd  = d.dt
LEFT JOIN h hm1 ON hm1.hd = d.dt - INTERVAL '1' DAY
LEFT JOIN h hm2 ON hm2.hd = d.dt - INTERVAL '2' DAY
LEFT JOIN h hp1 ON hp1.hd = d.dt + INTERVAL '1' DAY
LEFT JOIN h hp2 ON hp2.hd = d.dt + INTERVAL '2' DAY""", None),

    ("dim_route", EVENTS + """,
h AS (SELECT route, route_category, CAST(effective_date AS date) AS vf,
             lead(CAST(effective_date AS date)) OVER (PARTITION BY route ORDER BY effective_date) AS nxt
      FROM ref_route_history),
v AS (
  SELECT r.route, r.origin, r.destination, r.region, h.route_category,
         h.vf AS valid_from, coalesce(h.nxt, DATE '9999-12-31') AS valid_to, h.nxt IS NULL AS is_current
  FROM h JOIN ref_routes r ON r.route = h.route
  UNION ALL
  -- rutas que aparecen en eventos pero no en el archivo de referencia: una sola versión sin categoría
  SELECT x.route, split_part(x.route, '-', 1), split_part(x.route, '-', 2), 'UNKNOWN', 'UNCLASSIFIED',
         DATE '2023-01-01', DATE '9999-12-31', true
  FROM (SELECT DISTINCT coalesce(route, origin || '-' || destination) AS route FROM ev) x
  WHERE x.route IS NOT NULL AND x.route NOT IN (SELECT route FROM ref_routes)
)
SELECT CAST(row_number() OVER (ORDER BY route, valid_from) AS integer) AS route_key, route, origin, destination,
       region, route_category, valid_from, valid_to, is_current
FROM v""", None),

    ("dim_fare_class", EVENTS + """
SELECT CAST(row_number() OVER (ORDER BY fare_class) AS integer) AS fare_class_key, fare_class,
       CASE WHEN fare_class = 'BUSINESS' THEN 'BUSINESS' ELSE 'ECONOMY' END AS cabin
FROM (SELECT fare_class FROM (VALUES 'ECONOMY', 'BUSINESS') AS t(fare_class)
      UNION SELECT fare_class FROM ev WHERE fare_class IS NOT NULL)""", None),

    ("fact_search", EVENTS + """
SELECT ev.event_id, CAST(date_format(ev.ts, '%Y%m%d') AS integer) AS date_key, r.route_key,
       CAST(ev.results_count AS integer) AS results_count, 1 AS searches,
       ev.session_id, ev.travel_date, ev.event_ts,
       date_format(ev.ts, '%Y-%m') AS month
FROM ev
JOIN dim_route r ON r.route = ev.origin || '-' || ev.destination
                AND date(ev.ts) >= r.valid_from AND date(ev.ts) < r.valid_to   -- versión vigente ese día
WHERE ev.event_type = 'search_performed'""", "month"),

    ("fact_booking_event", EVENTS + """
SELECT ev.event_id, CAST(date_format(ev.ts, '%Y%m%d') AS integer) AS date_key, r.route_key, f.fare_class_key,
       ev.reservation_id, ev.event_type,
       CASE WHEN ev.event_type = 'reservation_created' THEN 1 ELSE 0 END AS bookings,
       CASE WHEN ev.event_type = 'reservation_cancelled' THEN 1 ELSE 0 END AS cancellations,
       CASE WHEN ev.event_type = 'reservation_created' THEN ev.price_cents ELSE -ev.price_cents END AS revenue_cents,
       ev.flight_id, ev.flight_date, ev.event_ts
FROM ev
JOIN dim_route r ON r.route = ev.route AND date(ev.ts) >= r.valid_from AND date(ev.ts) < r.valid_to
JOIN dim_fare_class f ON f.fare_class = ev.fare_class
WHERE ev.event_type IN ('reservation_created', 'reservation_cancelled')""", None),
]

_athena = boto3.client("athena", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_state = {"state": "SUCCEEDED", "job_id": None, "step": None, "error": None, "seconds": None}
_lock = threading.Lock()


def run_sql(sql: str, timeout: int = 600) -> str:
    qid = _athena.start_query_execution(QueryString=sql, QueryExecutionContext={"Database": DB},
                                        WorkGroup=WORKGROUP)["QueryExecutionId"]
    t0 = time.time()
    while True:
        st = _athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if st["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() - t0 > timeout:
            raise TimeoutError(f"Athena {qid} sigue en {st['State']}")
        time.sleep(1)
    if st["State"] != "SUCCEEDED":
        raise RuntimeError(f"{st['State']}: {st.get('StateChangeReason')}")
    return qid


def _delete_prefix(prefix: str):
    keys = []
    for page in _s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys += [{"Key": o["Key"]} for o in page.get("Contents", [])]
    for i in range(0, len(keys), 1000):
        _s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys[i:i + 1000]})


def rebuild():
    for sql in SETUP:
        _state["step"] = "setup"
        run_sql(sql)
    for name, select, part in TABLES:
        _state["step"] = name
        run_sql(f"DROP TABLE IF EXISTS {name}")
        _delete_prefix(f"star/{name}/")
        props = f"format = 'PARQUET', write_compression = 'SNAPPY', external_location = '{STAR}{name}/'"
        if part:
            props += f", partitioned_by = ARRAY['{part}']"
        run_sql(f"CREATE TABLE {name} WITH ({props}) AS {select}")


def _job(job_id: str):
    t0 = time.time()
    try:
        rebuild()
        _state.update(state="SUCCEEDED", step=None, error=None)
    except Exception as ex:
        log.exception("refresh-star falló")
        _state.update(state="FAILED", error=str(ex)[:500])
    _state["seconds"] = round(time.time() - t0, 1)


router = APIRouter()


@router.post("/admin/refresh-star", status_code=202)
def refresh_star():
    with _lock:
        if _state["state"] != "RUNNING":   # si ya corre uno, se devuelve ese mismo trabajo
            _state.update(state="RUNNING", job_id=str(uuid.uuid4()), error=None, seconds=None)
            threading.Thread(target=_job, args=(_state["job_id"],), daemon=True).start()
    return {"job_id": _state["job_id"]}


@router.get("/admin/refresh-status")
def refresh_status():
    return dict(_state)
