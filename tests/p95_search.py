"""p95 de GET /flights/search: 100 peticiones, 20 clientes concurrentes. Uso: BASE_URL=... python tests/p95_search.py"""
import os, statistics, time
from concurrent.futures import ThreadPoolExecutor
import requests

BASE = os.getenv("BASE_URL", "http://localhost:8000")
def one(i):
    d = f"2026-{6 + i % 5:02d}-{1 + i % 28:02d}"
    t = time.perf_counter()
    r = requests.get(f"{BASE}/flights/search", params={"origin": "BOG", "destination": "MDE", "date": d}, timeout=60)
    return time.perf_counter() - t, r.status_code
with ThreadPoolExecutor(20) as ex:
    res = list(ex.map(one, range(100)))
lat = sorted(x[0] for x in res)
p95 = lat[int(len(lat) * 0.95) - 1]
print(f"BASE_URL={BASE} peticiones=100 concurrencia=20 codigos={sorted(set(x[1] for x in res))}")
print(f"min={lat[0]*1000:.0f}ms media={statistics.mean(lat)*1000:.0f}ms p50={statistics.median(lat)*1000:.0f}ms p95={p95*1000:.0f}ms max={lat[-1]*1000:.0f}ms objetivo<=1000ms -> {'OK' if p95<=1 else 'FALLA'}")
