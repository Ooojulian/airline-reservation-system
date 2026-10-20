"""API de reservas aéreas (FastAPI + psycopg 3).

Concurrencia: cada reserva se hace en UNA transacción; las sillas se descuentan con un
UPDATE condicional (seats_available > 0) en orden ascendente de flight_id, lo que evita
sobreventa (el bloqueo de fila serializa) y deadlocks (orden global consistente).
flight_id es TEXT y lo asigna el cliente (ej. 'AV101-20260601').
"""
import re
import secrets
import string
import time as _time
from contextlib import asynccontextmanager
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field, field_validator

from . import db
from .analytics import router as analytics_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.pool.open()
    yield
    db.pool.close()


app = FastAPI(title="Sistema de Reservas Aéreas", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])
app.include_router(analytics_router)

CODE_ALPHABET = string.ascii_uppercase + string.digits
ROUTE_RE = re.compile(r"^[A-Z]{3}-[A-Z]{3}$")


# ----------------------------- Modelos -----------------------------
class ReservationIn(BaseModel):
    flight_id: str = Field(min_length=1, max_length=64)
    passenger_name: str = Field(min_length=1, max_length=200)
    fare_class: Literal["ECONOMY", "BUSINESS"]
    connection_flight_ids: list[str] = Field(default_factory=list, max_length=3)  # máx. 4 tramos
    document_id: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=200)


class FlightIn(BaseModel):
    flight_id: str = Field(min_length=1, max_length=64)
    route: str
    flight_date: date
    capacity: int = Field(gt=0)
    price_cents: int = Field(gt=0)
    flight_number: str | None = None
    aircraft_id: str | None = None
    departure_time: time | None = None

    @field_validator("route")
    @classmethod
    def _route(cls, v: str) -> str:
        v = v.strip().upper()
        if not ROUTE_RE.match(v) or v[:3] == v[4:]:
            raise ValueError("route debe ser 'AAA-BBB' con aeropuertos distintos")
        return v


# ----------------------------- Utilidades -----------------------------
@app.exception_handler(PoolTimeout)
async def pool_timeout_handler(request, exc):
    return JSONResponse(status_code=503, content={"detail": "base de datos saturada, reintente"})


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def segment_price(price_cents: int, multiplier: Decimal) -> int:
    return int((Decimal(price_cents) * multiplier).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def load_reservation(cur, reservation_id: str) -> dict | None:
    """Arma la respuesta completa de una reserva (usa el cursor de la transacción actual)."""
    cur.execute(
        """SELECT r.reservation_id, r.status, r.total_price_cents, r.created_at, r.updated_at,
                  fr.fare_class, fr.multiplier,
                  p.full_name, p.document_id, p.email
           FROM reservations r
           JOIN passengers p ON p.passenger_id = r.passenger_id
           JOIN fare_rules fr ON fr.fare_rule_id = r.fare_rule_id
           WHERE r.reservation_id = %s""",
        (reservation_id,),
    )
    r = cur.fetchone()
    if r is None:
        return None
    cur.execute(
        """SELECT s.segment_no, s.flight_id, f.flight_number, f.route_code AS route, rt.origin, rt.destination,
                  f.flight_date, f.departure_time, s.price_cents
           FROM reservation_segments s
           JOIN flights f ON f.flight_id = s.flight_id
           JOIN routes rt ON rt.route_code = f.route_code
           WHERE s.reservation_id = %s ORDER BY s.segment_no""",
        (reservation_id,),
    )
    segments = cur.fetchall()
    cur.execute(
        "SELECT payment_id, amount_cents, provider, provider_ref, status FROM payments WHERE reservation_id = %s",
        (reservation_id,),
    )
    return {
        "reservation_id": r["reservation_id"],
        "flight_id": segments[0]["flight_id"] if segments else None,  # primer tramo
        "status": r["status"],
        "passenger_name": r["full_name"],
        "document_id": r["document_id"],
        "email": r["email"],
        "fare_class": r["fare_class"],
        "multiplier": r["multiplier"],
        "total_price_cents": r["total_price_cents"],
        "segments": segments,
        "payment": cur.fetchone(),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


# ----------------------------- Endpoints -----------------------------
@app.get("/health")
def health():
    try:
        with db.pool.connection(timeout=5) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "db": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "db": "error"})


@app.get("/flights/search")
def search_flights(
    origin: str = Query(pattern=r"^[A-Za-z]{3}$"),
    destination: str = Query(pattern=r"^[A-Za-z]{3}$"),
    date: date = Query(),
):
    origin, destination = origin.upper(), destination.upper()
    if origin == destination:
        raise HTTPException(422, "origen y destino deben ser distintos")
    with db.pool.connection() as conn:
        cur = conn.execute(
            """SELECT f.flight_id, f.route_code AS route, f.flight_date, f.seats_available, f.price_cents,
                      f.flight_number, f.departure_time, f.capacity
               FROM flights f JOIN routes rt ON rt.route_code = f.route_code
               WHERE rt.origin = %s AND rt.destination = %s AND f.flight_date = %s
                 AND f.seats_available > 0
               ORDER BY f.departure_time, f.flight_id""",
            (origin, destination, date),
        )
        return cur.fetchall()


@app.get("/flights/{flight_id}")
def get_flight(flight_id: str):
    """Extra (no está en el SPEC): estado de un vuelo, útil para verificar seats_available."""
    with db.pool.connection() as conn:
        row = conn.execute(
            """SELECT f.flight_id, f.route_code AS route, f.flight_number, f.flight_date, f.departure_time,
                      f.capacity, f.seats_available, f.price_cents
               FROM flights f WHERE f.flight_id = %s""",
            (flight_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "vuelo no existe")
    return row


def _create_reservation(body: ReservationIn) -> dict:
    ids = [body.flight_id, *body.connection_flight_ids]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "tramos repetidos")

    # Una sola transacción; cualquier HTTPException hace ROLLBACK al salir del contexto.
    with db.pool.connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT f.flight_id, f.flight_date, f.price_cents, rt.origin, rt.destination,
                      CURRENT_DATE AS today
               FROM flights f JOIN routes rt ON rt.route_code = f.route_code
               WHERE f.flight_id = ANY(%s)""",
            (ids,),
        )
        found = {r["flight_id"]: r for r in cur.fetchall()}
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(404, f"vuelo(s) inexistente(s): {missing}")
        seq = [found[i] for i in ids]  # en el orden del itinerario

        for a, b in zip(seq, seq[1:]):
            if a["destination"] != b["origin"]:
                raise HTTPException(422, "los tramos no encadenan (destino != origen siguiente)")
            if b["flight_date"] < a["flight_date"]:
                raise HTTPException(422, "las fechas de los tramos retroceden")
        if seq[0]["flight_date"] < seq[0]["today"]:
            raise HTTPException(422, "el vuelo ya partió (fecha pasada)")

        # Tarifa: mayor min_days_before <= días que faltan para el primer tramo
        days_before = (seq[0]["flight_date"] - seq[0]["today"]).days
        cur.execute(
            """SELECT fare_rule_id, multiplier FROM fare_rules
               WHERE fare_class = %s AND min_days_before <= %s
               ORDER BY min_days_before DESC LIMIT 1""",
            (body.fare_class, days_before),
        )
        rule = cur.fetchone()

        # Descuento de sillas: orden ascendente de flight_id (evita deadlocks), todo-o-nada
        for fid in sorted(ids):
            cur.execute(
                """UPDATE flights SET seats_available = seats_available - 1
                   WHERE flight_id = %s AND seats_available > 0 AND flight_date >= CURRENT_DATE
                   RETURNING flight_id""",
                (fid,),
            )
            if cur.fetchone() is None:
                raise HTTPException(409, f"sin sillas disponibles en el vuelo {fid}")

        prices = [segment_price(s["price_cents"], rule["multiplier"]) for s in seq]
        total = sum(prices)

        cur.execute(
            "INSERT INTO passengers (full_name, document_id, email) VALUES (%s,%s,%s) RETURNING passenger_id",
            (body.passenger_name, body.document_id, body.email),
        )
        passenger_id = cur.fetchone()["passenger_id"]

        # Código de 6 caracteres; ON CONFLICT DO NOTHING permite reintentar sin abortar la transacción
        code = None
        for _ in range(20):
            candidate = new_code()
            cur.execute(
                """INSERT INTO reservations (reservation_id, passenger_id, fare_rule_id, status, total_price_cents)
                   VALUES (%s,%s,%s,'CONFIRMED',%s)
                   ON CONFLICT (reservation_id) DO NOTHING RETURNING reservation_id""",
                (candidate, passenger_id, rule["fare_rule_id"], total),
            )
            if cur.fetchone() is not None:
                code = candidate
                break
        if code is None:
            raise HTTPException(503, "no se pudo generar un código de reserva único, reintente")

        for n, (fid, price) in enumerate(zip(ids, prices), start=1):
            cur.execute(
                "INSERT INTO reservation_segments (reservation_id, segment_no, flight_id, price_cents) VALUES (%s,%s,%s,%s)",
                (code, n, fid, price),
            )
        cur.execute(
            "INSERT INTO payments (reservation_id, amount_cents, provider_ref, status) VALUES (%s,%s,%s,'APPROVED')",
            (code, total, f"SIM-{code}"),
        )
        return load_reservation(cur, code)


@app.post("/reservations", status_code=201)
def create_reservation(body: ReservationIn):
    # Ante deadlock/serialización (no esperados por el orden de bloqueo) se reintenta la transacción.
    for attempt in range(4):
        try:
            return _create_reservation(body)
        except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
            _time.sleep(0.05 * (attempt + 1))
    raise HTTPException(503, "contención alta, reintente")


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str):
    with db.pool.connection() as conn:
        res = load_reservation(conn.cursor(), reservation_id.strip().upper())
    if res is None:
        raise HTTPException(404, "reserva no existe")
    return res


@app.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: str):
    rid = reservation_id.strip().upper()
    with db.pool.connection() as conn:
        cur = conn.cursor()
        # UPDATE condicional: si dos cancelaciones compiten, solo una cambia la fila
        cur.execute(
            """UPDATE reservations SET status = 'CANCELLED', updated_at = now()
               WHERE reservation_id = %s AND status = 'CONFIRMED' RETURNING reservation_id""",
            (rid,),
        )
        if cur.fetchone() is None:
            cur.execute("SELECT 1 FROM reservations WHERE reservation_id = %s", (rid,))
            if cur.fetchone() is None:
                raise HTTPException(404, "reserva no existe")
            raise HTTPException(409, "la reserva ya estaba cancelada")

        cur.execute(
            "SELECT flight_id FROM reservation_segments WHERE reservation_id = %s ORDER BY flight_id",
            (rid,),
        )
        for row in sorted(cur.fetchall(), key=lambda r: r["flight_id"]):  # orden consistente con la reserva
            cur.execute(
                "UPDATE flights SET seats_available = seats_available + 1 WHERE flight_id = %s",
                (row["flight_id"],),
            )
        cur.execute(
            "UPDATE payments SET status = 'REFUNDED', updated_at = now() WHERE reservation_id = %s",
            (rid,),
        )
        return load_reservation(cur, rid)


@app.post("/admin/flights", status_code=201)
def create_flight(body: FlightIn):
    origin, destination = body.route[:3], body.route[4:]
    try:
        with db.pool.connection() as conn:
            cur = conn.cursor()
            # Aeropuertos y ruta se crean si no existen
            for code in (origin, destination):
                cur.execute("INSERT INTO airports (code, city) VALUES (%s,%s) ON CONFLICT DO NOTHING", (code, code))
            cur.execute(
                "INSERT INTO routes (route_code, origin, destination) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (body.route, origin, destination),
            )
            if body.flight_number:
                # flights.flight_number referencia scheduled_flights: se registra si no existe
                cur.execute(
                    """INSERT INTO scheduled_flights (flight_number, route_code, departure_time, days_of_week, aircraft_id)
                       VALUES (%s,%s,%s,'1234567',%s) ON CONFLICT (flight_number) DO NOTHING""",
                    (body.flight_number, body.route, body.departure_time, body.aircraft_id),
                )
            cur.execute(
                """INSERT INTO flights (flight_id, flight_number, route_code, aircraft_id, flight_date,
                                        departure_time, capacity, seats_available, price_cents)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (body.flight_id, body.flight_number, body.route, body.aircraft_id, body.flight_date,
                 body.departure_time, body.capacity, body.capacity, body.price_cents),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "el vuelo ya existe (flight_id o flight_number+fecha)")
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "aircraft_id inexistente")
    return body.model_dump(mode="json")
