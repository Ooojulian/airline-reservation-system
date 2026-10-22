"""Pruebas de clases tarifarias, sillas y check-in. Uso:  BASE_URL=http://localhost:8000 pytest -s tests/test_checkin.py

Vuelos de prueba por /admin/flights sin flight_number (los borra infra/limpiar_vuelos_prueba.sql).
Ventana abierta: salida en ~10 h (hora de Bogotá). Ventana cerrada: salida en 30 min.
"""
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
BOGOTA = timezone(timedelta(hours=-5))
PRICE = 30_000_000


def api(method, path, **kw):
    return requests.request(method, BASE_URL + path, timeout=90, **kw)


def mk_flight(when=None, route="BOG-MDE", capacity=150, flight_date=None):
    """when: datetime Bogotá de salida (vuelo abierto/cerrado); si no, fecha lejana (2030)."""
    body = {"flight_id": "K" + uuid.uuid4().hex[:10].upper(), "route": route, "capacity": capacity,
            "price_cents": PRICE}
    if when is not None:
        body["flight_date"] = when.date().isoformat()
        body["departure_time"] = when.strftime("%H:%M:%S")
    else:
        body["flight_date"] = (flight_date or date(2030, 6, 1)).isoformat()
    r = api("POST", "/admin/flights", json=body)
    assert r.status_code == 201, r.text
    return body["flight_id"]


def open_flight(**kw):
    return mk_flight(datetime.now(BOGOTA) + timedelta(hours=10), **kw)


def book(fid, fare="M", seat=None, name="Test Checkin"):
    body = {"flight_id": fid, "passenger_name": name, "fare_class": fare}
    if seat:
        body["seat"] = seat
    return api("POST", "/reservations", json=body)


def free_seats(fid):
    return api("GET", f"/flights/{fid}").json()["seats_available"]


def test_xs_con_silla_422():
    f = mk_flight()
    r = book(f, "XS", seat="10A")
    assert r.status_code == 422 and "check-in" in r.json()["detail"]
    assert free_seats(f) == 150


def test_alias_economy_y_business():
    f = mk_flight()
    r = book(f, "ECONOMY")
    assert r.status_code == 201
    j = r.json()
    assert j["status"] == "CONFIRMED" and j["flight_id"] == f and j["passenger_name"] == "Test Checkin"
    assert j["fare_class"] == "M" and j["cabin"] == "ECONOMICA" and j["seat"] is None
    g = api("GET", f"/reservations/{j['reservation_id']}").json()
    assert g["segments"][0]["route_type"] == "NACIONAL" and g["segments"][0]["checked_in"] is False
    assert g["segments"][0]["checkin_opens_at"] and g["segments"][0]["checkin_closes_at"]
    b = book(f, "BUSINESS").json()
    assert b["fare_class"] == "PR" and b["cabin"] == "PREMIUM"


def test_precios_por_clase():
    f = mk_flight()
    m = book(f, "M").json()["total_price_cents"]
    xs = book(f, "XS").json()["total_price_cents"]
    pr = book(f, "PR").json()["total_price_cents"]
    assert xs == round(m * 0.7) and pr == round(m * 2.2)


def test_pr_con_silla_y_conflicto():
    f = mk_flight()
    r = book(f, "PR", seat="1A")
    assert r.status_code == 201 and r.json()["seat"] == "1A"
    before = free_seats(f)
    r2 = book(f, "PR", seat="1A")
    assert r2.status_code == 409
    assert free_seats(f) == before  # rollback completo
    m = api("GET", f"/flights/{f}/seats").json()
    assert m["premium_rows"] == 2 and m["rows"] == 25 and len(m["seats"]) == 150
    assert {s["seat_no"]: s["status"] for s in m["seats"]}["1A"] == "TAKEN"


def test_silla_invalida_o_de_otra_cabina():
    f = mk_flight()
    assert book(f, "PR", seat="10A").status_code == 422   # PR solo Premium
    assert book(f, "L", seat="1A").status_code == 422     # L solo Económica
    assert book(f, "L", seat="99Z").status_code == 422
    assert book(f, "L", seat="5C").status_code == 201


def test_cambiar_silla_y_cancelar_libera():
    f = mk_flight()
    r = book(f, "PR", seat="2A").json()
    rid = r["reservation_id"]
    assert api("POST", f"/reservations/{rid}/seat", json={"seat": "2B"}).status_code == 200
    seats = {s["seat_no"]: s["status"] for s in api("GET", f"/flights/{f}/seats").json()["seats"]}
    assert seats["2A"] == "FREE" and seats["2B"] == "TAKEN"
    assert api("POST", f"/reservations/{rid}/cancel").status_code == 200
    seats = {s["seat_no"]: s["status"] for s in api("GET", f"/flights/{f}/seats").json()["seats"]}
    assert seats["2B"] == "FREE"
    assert book(f, "PR", seat="2B").status_code == 201
    m = book(f, "M").json()["reservation_id"]
    assert api("POST", f"/reservations/{m}/seat", json={"seat": "5A"}).status_code == 422


def test_checkin_fuera_de_ventana():
    f = mk_flight()  # 2030: aún no abre
    rid = book(f, "M").json()["reservation_id"]
    r = api("POST", f"/reservations/{rid}/checkin", json={"seat": "10A"})
    assert r.status_code == 409 and "abre" in r.json()["detail"]
    assert api("POST", f"/reservations/{rid}/checkin/hold", json={"seat": "10A"}).status_code == 409
    g = mk_flight(datetime.now(BOGOTA) + timedelta(minutes=30))  # ya cerró (1 h antes)
    rb = book(g, "M")
    assert rb.status_code == 201, rb.text
    rid = rb.json()["reservation_id"]
    r = api("POST", f"/reservations/{rid}/checkin", json={"seat": "10A"})
    assert r.status_code == 409 and "cerr" in r.json()["detail"]


def test_hold_concurrente_un_solo_ganador():
    f = open_flight()
    rids = [book(f, "M", name=f"Cliente {i}").json()["reservation_id"] for i in range(20)]
    codes = []
    barrier = threading.Barrier(20)

    def go(rid):
        barrier.wait()
        codes.append(api("POST", f"/reservations/{rid}/checkin/hold", json={"seat": "12C"}).status_code)

    ts = [threading.Thread(target=go, args=(r,)) for r in rids]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [200] + [409] * 19, codes
    assert not any(c >= 500 for c in codes)


def test_checkin_completo_y_repetido():
    f = open_flight()
    a = book(f, "M", name="Ana").json()["reservation_id"]
    b = book(f, "S", name="Beto").json()["reservation_id"]
    assert api("POST", f"/reservations/{a}/checkin", json={}).status_code == 422  # M debe traer silla
    h = api("POST", f"/reservations/{a}/checkin/hold", json={"seat": "7D"})
    assert h.status_code == 200 and h.json()["hold_seconds"] == 300
    # otra reserva no puede tomar ni hacer check-in en la silla bloqueada
    assert api("POST", f"/reservations/{b}/checkin", json={"seat": "7D"}).status_code == 409
    s = {x["seat_no"]: x["status"] for x in api("GET", f"/flights/{f}/seats").json()["seats"]}
    assert s["7D"] == "HELD"
    p = api("POST", f"/reservations/{a}/checkin", json={"seat": "7D"})
    assert p.status_code == 200
    pj = p.json()
    assert pj["seat"] == "7D" and pj["boarding_group"] == 3 and pj["passenger_name"] == "Ana" and pj["fare_class"] == "M"
    assert api("POST", f"/reservations/{a}/checkin", json={}).json() == pj  # repetido: mismo pase
    assert api("POST", f"/reservations/{b}/checkin", json={"seat": "7D"}).status_code == 409  # ya ocupada
    s = {x["seat_no"]: x["status"] for x in api("GET", f"/flights/{f}/seats").json()["seats"]}
    assert s["7D"] == "TAKEN"
    pb = api("POST", f"/reservations/{b}/checkin", json={"seat": "7E"}).json()
    assert pb["boarding_group"] == 4
    assert api("GET", f"/reservations/{a}").json()["segments"][0]["checked_in"] is True
    # PR con silla comprada: check-in sin silla
    c = book(f, "PR", seat="1F").json()["reservation_id"]
    pc = api("POST", f"/reservations/{c}/checkin", json={}).json()
    assert pc["seat"] == "1F" and pc["boarding_group"] == 1 and pc["cabin"] == "PREMIUM"


def test_checkin_misma_silla_concurrente():
    f = open_flight()
    rids = [book(f, "M").json()["reservation_id"] for _ in range(10)]
    codes = []
    barrier = threading.Barrier(10)

    def go(rid):
        barrier.wait()
        codes.append(api("POST", f"/reservations/{rid}/checkin", json={"seat": "20A"}).status_code)

    ts = [threading.Thread(target=go, args=(r,)) for r in rids]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [200] + [409] * 9, codes


def test_rutas_y_tipo_de_ruta():
    r = api("GET", "/routes")
    assert r.status_code == 200
    rs = {x["route_code"]: x for x in r.json()}
    assert set(rs["BOG-MIA"]) == {"route_code", "origin", "origin_city", "destination", "destination_city", "route_type"}
    assert rs["BOG-MIA"]["route_type"] == "INTERNACIONAL" and rs["BOG-MDE"]["route_type"] == "NACIONAL"
    for code in ["BOG-BGA", "BGA-BOG", "BOG-ADZ", "LET-BOG", "MDE-BAQ", "BAQ-MDE", "PSO-BOG", "PEI-BOG"]:
        assert code in rs and rs[code]["route_type"] == "NACIONAL"
    keys = [(x["origin"], x["destination"]) for x in r.json()]
    assert keys == sorted(keys)
    d = (date.today() + timedelta(days=20)).isoformat()
    fl = api("GET", "/flights/search", params={"origin": "BOG", "destination": "MIA", "date": d}).json()
    assert fl and all(x["route_type"] == "INTERNACIONAL" for x in fl)
    fl = api("GET", "/flights/search", params={"origin": "BOG", "destination": "ADZ", "date": d}).json()
    assert fl and fl[0]["route_type"] == "NACIONAL"


def test_busqueda_solo_vuelos_de_la_ruta_exacta():
    d = (date.today() + timedelta(days=15)).isoformat()
    for x in api("GET", "/routes").json():
        fl = api("GET", "/flights/search", params={"origin": x["origin"], "destination": x["destination"], "date": d}).json()
        assert fl, x["route_code"]
        assert all(f["route"] == x["route_code"] and f["route_type"] == x["route_type"] for f in fl), x["route_code"]
        assert len({f["flight_number"] for f in fl}) == len(fl)
    bog_mde = api("GET", "/flights/search", params={"origin": "BOG", "destination": "MDE", "date": d}).json()
    assert all(f["route"] == "BOG-MDE" and f["route_type"] == "NACIONAL" for f in bog_mde)
