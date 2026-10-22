"""Pruebas de la API. Uso:  BASE_URL=http://IP:8000 pytest -s tests/test_api.py

Los vuelos de prueba se crean vía /admin/flights con fecha en 2030 (aleatoria) e id único,
así no chocan con el seed (2026) ni con ejecuciones anteriores.
"""
import os
import random
import threading
import uuid
from collections import Counter
from datetime import date, timedelta

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 90
PRICE = 30_000_000  # 300.000 COP en centavos
RESULTS = []        # filas de la tabla de concurrencia


def api(method, path, **kw):
    return requests.request(method, BASE_URL + path, timeout=TIMEOUT, **kw)


def far_date():
    return date(2030, 1, 1) + timedelta(days=random.randint(0, 364))


def mk_flight(route="BOG-MDE", capacity=10, flight_date=None, price=PRICE):
    body = {
        "flight_id": "T" + uuid.uuid4().hex[:10].upper(),
        "route": route,
        "flight_date": (flight_date or far_date()).isoformat(),
        "capacity": capacity,
        "price_cents": price,
    }
    r = api("POST", "/admin/flights", json=body)
    assert r.status_code == 201, r.text
    return body


def book(flight_id, name="Test User", fare="ECONOMY", conn=None):
    return api("POST", "/reservations", json={
        "flight_id": flight_id, "passenger_name": name, "fare_class": fare,
        "connection_flight_ids": conn or [],
    })


def seats(flight_id):
    r = api("GET", f"/flights/{flight_id}")
    assert r.status_code == 200, r.text
    return r.json()["seats_available"]


@pytest.fixture(scope="module", autouse=True)
def print_table():
    yield
    if RESULTS:
        print("\n\nRESULTADOS DE CONCURRENCIA (50 solicitudes simultáneas por ronda)")
        print(f"{'ronda':>5} {'capacity':>8} {'201':>4} {'409':>4} {'5xx':>4} {'otros':>6} {'sillas_finales':>15}  resultado")
        for row in RESULTS:
            print("{:>5} {:>8} {:>4} {:>4} {:>4} {:>6} {:>15}  {}".format(*row))


# ----------------------------- Funcionales -----------------------------
def test_health():
    r = api("GET", "/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


def test_search():
    f = mk_flight("BOG-MDE", 3)
    r = api("GET", "/flights/search", params={"origin": "BOG", "destination": "MDE", "date": f["flight_date"]})
    assert r.status_code == 200
    found = [x for x in r.json() if x["flight_id"] == f["flight_id"]]
    assert len(found) == 1
    assert found[0]["seats_available"] == 3 and found[0]["route"] == "BOG-MDE"
    assert found[0]["price_cents"] == PRICE
    # parámetros inválidos
    assert api("GET", "/flights/search", params={"origin": "BOG", "destination": "MDE", "date": "no-es-fecha"}).status_code == 422
    assert api("GET", "/flights/search", params={"origin": "BO", "destination": "MDE", "date": "2030-01-01"}).status_code == 422
    assert api("GET", "/flights/search", params={"origin": "BOG"}).status_code == 422


def test_reserva_directa():
    f = mk_flight("BOG-MDE", 5)
    r = book(f["flight_id"], "Ana Directa")
    assert r.status_code == 201, r.text
    d = r.json()
    assert len(d["reservation_id"]) == 6 and d["reservation_id"].isalnum()
    assert d["status"] == "CONFIRMED" and d["flight_id"] == f["flight_id"]
    assert d["passenger_name"] == "Ana Directa" and d["fare_class"] == "M"
    assert d["total_price_cents"] == PRICE  # fecha lejana => multiplicador 1.00
    assert len(d["segments"]) == 1
    assert d["payment"]["status"] == "APPROVED"
    assert seats(f["flight_id"]) == 4


def test_reserva_con_escala():
    d0 = far_date()
    a = mk_flight("BOG-MDE", 5, d0, 30_000_000)
    b = mk_flight("MDE-CTG", 5, d0, 20_000_000)
    r = book(a["flight_id"], "Beto Escala", "BUSINESS", [b["flight_id"]])
    assert r.status_code == 201, r.text
    d = r.json()
    assert [s["flight_id"] for s in d["segments"]] == [a["flight_id"], b["flight_id"]]
    assert d["total_price_cents"] == int(50_000_000 * 2.2)  # BUSINESS (alias de PR) lejano = 2.20
    assert seats(a["flight_id"]) == 4 and seats(b["flight_id"]) == 4


def test_tramos_que_no_encadenan_422():
    d0 = far_date()
    a = mk_flight("BOG-MDE", 5, d0)
    b = mk_flight("BOG-CLO", 5, d0)  # sale de BOG, no de MDE
    r = book(a["flight_id"], conn=[b["flight_id"]])
    assert r.status_code == 422
    assert seats(a["flight_id"]) == 5 and seats(b["flight_id"]) == 5


def test_vuelo_inexistente_404():
    assert book("NO-EXISTE-" + uuid.uuid4().hex).status_code == 404


def test_consultar_reserva():
    f = mk_flight()
    created = book(f["flight_id"], "Carla Consulta").json()
    r = api("GET", f"/reservations/{created['reservation_id']}")
    assert r.status_code == 200
    assert r.json()["reservation_id"] == created["reservation_id"]
    assert r.json()["passenger_name"] == "Carla Consulta"
    assert api("GET", "/reservations/does-not-exist-" + uuid.uuid4().hex).status_code == 404


def test_cancelar_y_cancelar_dos_veces_409():
    f = mk_flight("BOG-MDE", 2)
    rid = book(f["flight_id"]).json()["reservation_id"]
    assert seats(f["flight_id"]) == 1
    r = api("POST", f"/reservations/{rid}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED" and r.json()["payment"]["status"] == "REFUNDED"
    assert seats(f["flight_id"]) == 2  # silla devuelta
    assert api("POST", f"/reservations/{rid}/cancel").status_code == 409
    assert seats(f["flight_id"]) == 2  # no se devuelve dos veces
    assert api("POST", "/reservations/ZZZZZZ/cancel").status_code == 404


def test_multitramo_todo_o_nada():
    d0 = far_date()
    a = mk_flight("BOG-MDE", 5, d0)
    b = mk_flight("MDE-CTG", 1, d0)
    assert book(b["flight_id"]).status_code == 201     # agota las sillas del segundo tramo
    assert seats(b["flight_id"]) == 0
    r = book(a["flight_id"], conn=[b["flight_id"]])
    assert r.status_code == 409
    assert seats(a["flight_id"]) == 5                  # el primer tramo conserva sus sillas
    assert seats(b["flight_id"]) == 0


def test_flight_duplicado_409():
    f = mk_flight()
    assert api("POST", "/admin/flights", json=f).status_code == 409


# ----------------------------- Concurrencia -----------------------------
N_REQ = 50


@pytest.mark.parametrize("capacity,ronda", [(c, i) for c in (1, 5) for i in range(1, 6)])
def test_concurrencia_sin_sobreventa(capacity, ronda):
    f = mk_flight("BOG-MDE", capacity)
    barrier = threading.Barrier(N_REQ)
    codes = [None] * N_REQ
    bodies = [None] * N_REQ

    def worker(i):
        s = requests.Session()
        try:
            barrier.wait(timeout=60)
            r = s.post(BASE_URL + "/reservations", timeout=TIMEOUT, json={
                "flight_id": f["flight_id"], "passenger_name": f"Concurrente {i}", "fare_class": "ECONOMY",
            })
            codes[i] = r.status_code
            if r.status_code == 201:
                bodies[i] = r.json()["reservation_id"]
        except Exception:  # noqa: BLE001 - se cuenta como fallo
            codes[i] = -1

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_REQ)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    c = Counter(codes)
    n5xx = sum(v for k, v in c.items() if k >= 500 or k == -1)
    otros = N_REQ - c[201] - c[409] - n5xx
    final = seats(f["flight_id"])
    ok = c[201] == capacity and c[409] == N_REQ - capacity and n5xx == 0 and final == 0
    RESULTS.append((f"{capacity}-{ronda}", capacity, c[201], c[409], n5xx, otros, final, "OK" if ok else "FALLO"))

    assert n5xx == 0, f"errores 5xx/red: {c}"
    assert c[201] == capacity, f"se esperaban {capacity} reservas, hubo {c[201]}: {c}"
    assert c[409] == N_REQ - capacity, c
    assert final == 0
    # ya no aparece en la búsqueda (solo lista vuelos con sillas)
    r = api("GET", "/flights/search", params={"origin": "BOG", "destination": "MDE", "date": f["flight_date"]})
    assert f["flight_id"] not in [x["flight_id"] for x in r.json()]
    # códigos de reserva únicos
    ids = [b for b in bodies if b]
    assert len(set(ids)) == len(ids) == capacity
