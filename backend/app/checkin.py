"""Clases tarifarias, mapa de sillas y check-in (RF-20 a RF-24, RF-81 a RF-83).

La garantía de "una silla = una reserva" es la clave primaria (flight_id, seat_no) de
seat_assignments. seat_holds es solo un bloqueo temporal de 5 minutos; vencido = libre
(se limpia de forma perezosa, sin proceso de barrido).
"""
import re
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import db

router = APIRouter()

FARE_ALIAS = {"ECONOMY": "M", "BUSINESS": "PR"}
BOARDING_GROUP = {"PR": 1, "L": 2, "M": 3, "S": 4, "XS": 4}
HOLD_SECONDS = 300
LETTERS = "ABCDEF"
SEAT_RE = re.compile(r"^(\d{1,3})([A-F])$")
BOGOTA = timezone(timedelta(hours=-5))  # Colombia no tiene horario de verano
MSG_CHECKIN_SEAT = "esta clase elige silla en el check-in"

# Salida real (America/Bogota); sin hora de salida se asume 12:00
DEPARTURE_SQL = "((f.flight_date + COALESCE(f.departure_time, time '12:00')) AT TIME ZONE 'America/Bogota')"
CLOSE_HOURS_SQL = "(CASE rt.route_type WHEN 'INTERNACIONAL' THEN 2 ELSE 1 END)"


def canonical_fare(fare_class: str) -> str:
    return FARE_ALIAS.get(fare_class, fare_class)


# ----------------------------- Mapa de sillas -----------------------------
def seat_layout(capacity: int) -> tuple[int, int]:
    """(filas, filas premium). Filas de 6; premium = floor(capacity * 0.08 / 6)."""
    return -(-capacity // 6), (capacity * 8) // 600


def seat_cabin(capacity: int, seat_no: str) -> str | None:
    """Cabina de la silla, o None si no existe en el avión."""
    m = SEAT_RE.match(seat_no or "")
    if not m or int(m.group(1)) < 1:
        return None
    row, letter = int(m.group(1)), m.group(2)
    if (row - 1) * 6 + LETTERS.index(letter) >= capacity:
        return None
    return "PREMIUM" if row <= seat_layout(capacity)[1] else "ECONOMICA"


def validate_seat(capacity: int, fare_cabin: str, seat_no: str) -> str:
    """Devuelve la silla normalizada o lanza 422 si no existe / no es de la cabina de la tarifa."""
    seat_no = (seat_no or "").strip().upper()
    cabin = seat_cabin(capacity, seat_no)
    if cabin is None:
        raise HTTPException(422, f"la silla {seat_no} no existe en este vuelo")
    premium_rows = seat_layout(capacity)[1]
    if fare_cabin == "PREMIUM" and premium_rows > 0 and cabin != "PREMIUM":
        raise HTTPException(422, "la tarifa Premium solo puede elegir sillas de la cabina Premium")
    if fare_cabin == "ECONOMICA" and cabin == "PREMIUM":
        raise HTTPException(422, "esta tarifa solo puede elegir sillas de la cabina Económica")
    return seat_no


def fmt_bogota(dt: datetime) -> str:
    return dt.astimezone(BOGOTA).strftime("%Y-%m-%d %H:%M") + " (hora de Bogotá)"


# ----------------------------- Reserva completa -----------------------------
def load_reservation(cur, reservation_id: str) -> dict | None:
    """Arma la respuesta completa de una reserva (usa el cursor de la transacción actual)."""
    cur.execute(
        """SELECT r.reservation_id, r.status, r.total_price_cents, r.created_at, r.updated_at,
                  fr.fare_class, fr.multiplier, fr.cabin, fr.seat_at_purchase,
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
        f"""SELECT s.segment_no, s.flight_id, f.flight_number, f.route_code AS route, rt.origin, rt.destination,
                   f.flight_date, f.departure_time, s.price_cents,
                   sa.seat_no AS seat, (s.checked_in_at IS NOT NULL) AS checked_in, s.checked_in_at,
                   {DEPARTURE_SQL} - interval '24 hours' AS checkin_opens_at,
                   {DEPARTURE_SQL} - {CLOSE_HOURS_SQL} * interval '1 hour' AS checkin_closes_at,
                   rt.route_type
            FROM reservation_segments s
            JOIN flights f ON f.flight_id = s.flight_id
            JOIN routes rt ON rt.route_code = f.route_code
            LEFT JOIN seat_assignments sa ON sa.flight_id = s.flight_id AND sa.reservation_id = s.reservation_id
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
        "cabin": r["cabin"],
        "seat": segments[0]["seat"] if segments else None,
        "multiplier": r["multiplier"],
        "total_price_cents": r["total_price_cents"],
        "segments": segments,
        "payment": cur.fetchone(),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


# ----------------------------- Asignación de silla -----------------------------
def assign_seat(cur, rid: str, flight_id: str, seat_no: str) -> None:
    """Asigna (o cambia) la silla de la reserva en el vuelo. 409 si otra reserva la tiene o la bloquea.
    El INSERT sobre la PK (flight_id, seat_no) es la defensa final contra la doble asignación."""
    cur.execute(
        "SELECT reservation_id FROM seat_assignments WHERE flight_id = %s AND seat_no = %s",
        (flight_id, seat_no),
    )
    row = cur.fetchone()
    if row is not None and row["reservation_id"] != rid:
        raise HTTPException(409, f"la silla {seat_no} ya está ocupada")
    cur.execute(
        "SELECT 1 FROM seat_holds WHERE flight_id = %s AND seat_no = %s AND reservation_id <> %s AND expires_at > now()",
        (flight_id, seat_no, rid),
    )
    if cur.fetchone() is not None:
        raise HTTPException(409, f"la silla {seat_no} está bloqueada por otro pasajero")
    # Silla previa propia (cambio de silla) y bloqueos propios / vencidos sobre esta silla
    cur.execute("DELETE FROM seat_assignments WHERE flight_id = %s AND reservation_id = %s", (flight_id, rid))
    cur.execute(
        "DELETE FROM seat_holds WHERE flight_id = %s AND (reservation_id = %s OR (seat_no = %s AND expires_at <= now()))",
        (flight_id, rid, seat_no),
    )
    try:
        cur.execute(
            "INSERT INTO seat_assignments (flight_id, seat_no, reservation_id) VALUES (%s,%s,%s)",
            (flight_id, seat_no, rid),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, f"la silla {seat_no} ya está ocupada")


def release_seats(cur, rid: str) -> None:
    cur.execute("DELETE FROM seat_assignments WHERE reservation_id = %s", (rid,))
    cur.execute("DELETE FROM seat_holds WHERE reservation_id = %s", (rid,))


# ----------------------------- Contexto de reserva + tramo -----------------------------
def get_context(cur, reservation_id: str, flight_id: str | None) -> dict:
    """Bloquea la reserva (FOR UPDATE) y devuelve reserva + tramo con su ventana de check-in."""
    rid = reservation_id.strip().upper()
    cur.execute(
        """SELECT r.reservation_id, r.status, fr.fare_class, fr.cabin, fr.seat_at_purchase, p.full_name
           FROM reservations r
           JOIN fare_rules fr ON fr.fare_rule_id = r.fare_rule_id
           JOIN passengers p ON p.passenger_id = r.passenger_id
           WHERE r.reservation_id = %s FOR UPDATE OF r""",
        (rid,),
    )
    res = cur.fetchone()
    if res is None:
        raise HTTPException(404, "reserva no existe")
    cur.execute(
        f"""SELECT s.segment_no, s.flight_id, s.checked_in_at, f.flight_number, f.route_code AS route,
                   f.capacity, {DEPARTURE_SQL} AS departure,
                   {DEPARTURE_SQL} - interval '24 hours' AS opens_at,
                   {DEPARTURE_SQL} - {CLOSE_HOURS_SQL} * interval '1 hour' AS closes_at,
                   now() AS now
            FROM reservation_segments s
            JOIN flights f ON f.flight_id = s.flight_id
            JOIN routes rt ON rt.route_code = f.route_code
            WHERE s.reservation_id = %s AND (%s::text IS NULL OR s.flight_id = %s)
            ORDER BY s.segment_no LIMIT 1""",
        (rid, flight_id, flight_id),
    )
    seg = cur.fetchone()
    if seg is None:
        raise HTTPException(404 if flight_id else 409, "la reserva no tiene ese tramo")
    return {**res, "seg": seg, "rid": rid}


def require_confirmed(ctx: dict) -> None:
    if ctx["status"] != "CONFIRMED":
        raise HTTPException(409, "la reserva está cancelada")


def require_window_open(seg: dict) -> None:
    if seg["now"] < seg["opens_at"]:
        raise HTTPException(409, f"el check-in aún no abre: abre el {fmt_bogota(seg['opens_at'])}")
    if seg["now"] >= seg["closes_at"]:
        raise HTTPException(409, f"el check-in ya cerró: cerró el {fmt_bogota(seg['closes_at'])}")


def boarding_pass(cur, ctx: dict) -> dict:
    seg = ctx["seg"]
    cur.execute(
        "SELECT seat_no FROM seat_assignments WHERE flight_id = %s AND reservation_id = %s",
        (seg["flight_id"], ctx["rid"]),
    )
    row = cur.fetchone()
    return {
        "reservation_id": ctx["rid"],
        "passenger_name": ctx["full_name"],
        "flight_id": seg["flight_id"],
        "flight_number": seg["flight_number"],
        "route": seg["route"],
        "departure": seg["departure"],
        "seat": row["seat_no"] if row else None,
        "cabin": ctx["cabin"],
        "fare_class": ctx["fare_class"],
        "boarding_group": BOARDING_GROUP[ctx["fare_class"]],
    }


# ----------------------------- Endpoints -----------------------------
class SeatIn(BaseModel):
    seat: str = Field(min_length=2, max_length=4)
    flight_id: str | None = Field(default=None, max_length=64)  # tramo; por defecto el primero


class CheckinIn(BaseModel):
    seat: str | None = Field(default=None, max_length=4)
    flight_id: str | None = Field(default=None, max_length=64)


@router.get("/flights/{flight_id}/seats")
def flight_seats(flight_id: str, reservation_id: str | None = Query(default=None, max_length=6)):
    with db.pool.connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT capacity FROM flights WHERE flight_id = %s", (flight_id,))
        f = cur.fetchone()
        if f is None:
            raise HTTPException(404, "vuelo no existe")
        cap = f["capacity"]
        cur.execute("SELECT seat_no, reservation_id FROM seat_assignments WHERE flight_id = %s", (flight_id,))
        taken = {r["seat_no"]: r["reservation_id"] for r in cur.fetchall()}
        cur.execute(
            "SELECT seat_no, reservation_id FROM seat_holds WHERE flight_id = %s AND expires_at > now()",
            (flight_id,),
        )
        held = {r["seat_no"]: r["reservation_id"] for r in cur.fetchall()}
    rows, premium_rows = seat_layout(cap)
    me = (reservation_id or "").strip().upper() or None
    seats = []
    for n in range(cap):
        seat_no = f"{n // 6 + 1}{LETTERS[n % 6]}"
        status = "TAKEN" if seat_no in taken else "HELD" if seat_no in held else "FREE"
        item = {"seat_no": seat_no, "cabin": "PREMIUM" if n // 6 + 1 <= premium_rows else "ECONOMICA", "status": status}
        if me:
            item["mine"] = taken.get(seat_no) == me or held.get(seat_no) == me
        seats.append(item)
    return {"flight_id": flight_id, "rows": rows, "letters": list(LETTERS), "premium_rows": premium_rows, "seats": seats}


@router.post("/reservations/{reservation_id}/seat")
def set_seat(reservation_id: str, body: SeatIn):
    with db.pool.connection() as conn:
        cur = conn.cursor()
        ctx = get_context(cur, reservation_id, body.flight_id)
        require_confirmed(ctx)
        if not ctx["seat_at_purchase"]:
            raise HTTPException(422, MSG_CHECKIN_SEAT)
        seg = ctx["seg"]
        if seg["now"] >= seg["closes_at"]:
            raise HTTPException(409, f"el check-in ya cerró: cerró el {fmt_bogota(seg['closes_at'])}")
        seat = validate_seat(seg["capacity"], ctx["cabin"], body.seat)
        assign_seat(cur, ctx["rid"], seg["flight_id"], seat)
        return {"reservation_id": ctx["rid"], "flight_id": seg["flight_id"], "seat": seat,
                "fare_class": ctx["fare_class"], "cabin": ctx["cabin"]}


@router.post("/reservations/{reservation_id}/checkin/hold")
def hold_seat(reservation_id: str, body: SeatIn):
    with db.pool.connection() as conn:
        cur = conn.cursor()
        ctx = get_context(cur, reservation_id, body.flight_id)
        require_confirmed(ctx)
        seg = ctx["seg"]
        require_window_open(seg)
        seat = validate_seat(seg["capacity"], ctx["cabin"], body.seat)
        # Insert-or-take-over: solo gana si la silla no tiene dueño y el bloqueo no existe, venció o es propio
        cur.execute(
            """INSERT INTO seat_holds (flight_id, seat_no, reservation_id, expires_at)
               SELECT %s, %s, %s, now() + make_interval(secs => %s)
               WHERE NOT EXISTS (SELECT 1 FROM seat_assignments
                                 WHERE flight_id = %s AND seat_no = %s AND reservation_id <> %s)
               ON CONFLICT (flight_id, seat_no) DO UPDATE
                 SET reservation_id = EXCLUDED.reservation_id, expires_at = EXCLUDED.expires_at
                 WHERE seat_holds.expires_at < now() OR seat_holds.reservation_id = EXCLUDED.reservation_id
               RETURNING expires_at""",
            (seg["flight_id"], seat, ctx["rid"], HOLD_SECONDS, seg["flight_id"], seat, ctx["rid"]),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(409, f"la silla {seat} no está disponible (ocupada o bloqueada por otro pasajero)")
        # Un solo bloqueo por reserva y tramo
        cur.execute(
            "DELETE FROM seat_holds WHERE flight_id = %s AND reservation_id = %s AND seat_no <> %s",
            (seg["flight_id"], ctx["rid"], seat),
        )
        return {"reservation_id": ctx["rid"], "flight_id": seg["flight_id"], "seat": seat,
                "expires_at": row["expires_at"], "hold_seconds": HOLD_SECONDS}


@router.post("/reservations/{reservation_id}/checkin")
def do_checkin(reservation_id: str, body: CheckinIn | None = None):
    body = body or CheckinIn()
    with db.pool.connection() as conn:
        cur = conn.cursor()
        ctx = get_context(cur, reservation_id, body.flight_id)
        require_confirmed(ctx)
        seg = ctx["seg"]
        if seg["checked_in_at"] is not None:  # idempotente: mismo pase
            return boarding_pass(cur, ctx)
        require_window_open(seg)
        cur.execute(
            "SELECT 1 FROM seat_assignments WHERE flight_id = %s AND reservation_id = %s",
            (seg["flight_id"], ctx["rid"]),
        )
        has_seat = cur.fetchone() is not None
        if body.seat:
            assign_seat(cur, ctx["rid"], seg["flight_id"], validate_seat(seg["capacity"], ctx["cabin"], body.seat))
        elif not has_seat:
            raise HTTPException(422, "debe elegir una silla para hacer el check-in")
        cur.execute(
            "UPDATE reservation_segments SET checked_in_at = now() WHERE reservation_id = %s AND flight_id = %s",
            (ctx["rid"], seg["flight_id"]),
        )
        cur.execute("DELETE FROM seat_holds WHERE reservation_id = %s AND flight_id = %s", (ctx["rid"], seg["flight_id"]))
        return boarding_pass(cur, ctx)
