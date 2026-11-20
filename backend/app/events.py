"""Parcial 3: eventos hacia Kinesis y vista en vivo (GET /analytics/live).

Publicación:
- Búsquedas: se encolan y un hilo las envía en lotes con PutRecords (no frenan la respuesta).
  Si la cola se llena se descarta la búsqueda: perder una búsqueda es aceptable, frenar al usuario no.
- Reservas y cancelaciones: se envían después del COMMIT con PutRecord y reintentos. Si Kinesis falla
  igual van a la cola para reintentar (en producción se usaría un outbox en la base de datos).

Vista en vivo: un hilo consumidor lee el stream (GetRecords cada segundo) y guarda los eventos
recientes en memoria. /analytics/live cuenta por event_ts, no por hora de llegada.
"""
import json
import logging
import os
import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from fastapi import APIRouter, Query

log = logging.getLogger("events")
STREAM = os.getenv("KINESIS_STREAM", "")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MAX_WINDOW_MIN = 60

_kinesis = boto3.client("kinesis", region_name=REGION,
                        config=Config(retries={"max_attempts": 5, "mode": "standard"}, max_pool_connections=20))
_queue: "queue.Queue[tuple[str, bytes]]" = queue.Queue(maxsize=50_000)


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(event_type: str, **fields) -> dict:
    return {"event_id": str(uuid.uuid4()), "event_type": event_type, "event_ts": now_ts(), "schema_version": 1, **fields}


# ----------------------------------------------------------------------------- publicación
def publish_search(session_id: str | None, origin: str, destination: str, travel_date: str, results_count: int):
    if not STREAM:
        return
    e = _envelope("search_performed", session_id=session_id or str(uuid.uuid4()), origin=origin,
                  destination=destination, travel_date=travel_date, results_count=int(results_count))
    try:
        _queue.put_nowait((e["session_id"], json.dumps(e).encode()))
    except queue.Full:
        log.warning("cola llena: se descarta un evento de búsqueda")


def fare_for_event(fare: str) -> str:
    """Las clases internas (XS/S/M/L/PR) se publican como ECONOMY o BUSINESS (contrato)."""
    return "BUSINESS" if fare in ("PR", "BUSINESS") else "ECONOMY"


def publish_reservation(event_type: str, res: dict):
    """res: reserva ya guardada (load_reservation). Se llama después del COMMIT."""
    if not STREAM:
        return
    seg = res["segments"][0]
    e = _envelope(event_type, reservation_id=res["reservation_id"], flight_id=seg["flight_id"], route=seg["route"],
                  flight_date=str(seg["flight_date"]), fare_class=fare_for_event(res["fare_class"]),
                  price_cents=int(res["total_price_cents"]))
    data = json.dumps(e).encode()
    try:
        _kinesis.put_record(StreamName=STREAM, PartitionKey=e["flight_id"], Data=data)
    except Exception:
        log.exception("put_record falló, el evento queda en la cola para reintento")
        _queue.put((e["flight_id"], data))


def _sender():
    while True:
        batch = [_queue.get()]
        t0 = time.time()
        while len(batch) < 500 and time.time() - t0 < 0.2:   # lote de hasta 500 o 200 ms
            try:
                batch.append(_queue.get(timeout=0.05))
            except queue.Empty:
                pass
        for attempt in range(8):
            try:
                r = _kinesis.put_records(StreamName=STREAM,
                                         Records=[{"PartitionKey": pk, "Data": d} for pk, d in batch])
                if not r["FailedRecordCount"]:
                    break
                batch = [b for b, x in zip(batch, r["Records"]) if "ErrorCode" in x]
            except Exception:
                log.exception("put_records falló")
            time.sleep(0.2 * (attempt + 1))


# ----------------------------------------------------------------------------- vista en vivo
_recent: deque = deque()          # (event_ts datetime, event_type, event_id), ordenado por llegada
_seen: set = set()
_lock = threading.Lock()


def _parse_ts(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _consumer():
    """Un solo shard (R11). Arranca leyendo desde hace MAX_WINDOW_MIN para reconstruir el estado."""
    while True:
        try:
            shard = _kinesis.list_shards(StreamName=STREAM)["Shards"][-1]["ShardId"]
            since = datetime.now(timezone.utc) - timedelta(minutes=MAX_WINDOW_MIN)
            it = _kinesis.get_shard_iterator(StreamName=STREAM, ShardId=shard, ShardIteratorType="AT_TIMESTAMP",
                                             Timestamp=since)["ShardIterator"]
            while it:
                r = _kinesis.get_records(ShardIterator=it, Limit=10_000)
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=MAX_WINDOW_MIN)
                with _lock:
                    for rec in r["Records"]:
                        try:
                            e = json.loads(rec["Data"])
                        except Exception:
                            continue
                        ts = _parse_ts(e.get("event_ts", ""))
                        eid = e.get("event_id")
                        if ts is None or ts < cutoff or eid in _seen:   # eventos viejos (replay) no cuentan
                            continue
                        _seen.add(eid)
                        _recent.append((ts, e.get("event_type"), eid))
                    while _recent and _recent[0][0] < cutoff:
                        _seen.discard(_recent.popleft()[2])
                it = r.get("NextShardIterator")
                time.sleep(1.0)   # 1 GetRecords/s: deja cupo (5/s por shard) para Firehose y otros lectores
        except Exception:
            log.exception("consumidor de Kinesis falló, reintento en 5 s")
            time.sleep(5)


def start():
    if not STREAM:
        log.warning("KINESIS_STREAM no definido: eventos desactivados")
        return
    threading.Thread(target=_sender, daemon=True, name="kinesis-sender").start()
    threading.Thread(target=_consumer, daemon=True, name="kinesis-consumer").start()


router = APIRouter()


@router.get("/analytics/live")
def live(window: int = Query(5, ge=1, le=MAX_WINDOW_MIN)):
    now = datetime.now(timezone.utc)
    start_ts = now - timedelta(minutes=window)
    c = {"search_performed": 0, "reservation_created": 0, "reservation_cancelled": 0}
    with _lock:
        for ts, et, _ in _recent:
            if start_ts <= ts <= now + timedelta(seconds=5) and et in c:
                c[et] += 1
    s, r = c["search_performed"], c["reservation_created"]
    return {"window_minutes": window, "searches": s, "reservations": r,
            "cancellations": c["reservation_cancelled"], "conversion": round(r / s, 6) if s else 0.0}
