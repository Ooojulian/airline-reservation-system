# Sistema de Reservas Aéreas (Parcial 1, Big Data)

API FastAPI sobre PostgreSQL 16 (RDS db.t3.micro), con modelo transaccional (`airline_oltp`),
modelo analítico (`airline_olap`) y ETL en AWS Glue. Nada se ejecuta en local: todo corre en AWS.

## Estructura
```
backend/
  schema.sql        DDL de airline_oltp (10 tablas, CHECKs, índices, 6 fare_rules)
  schema_olap.sql   fact_route_month, fact_fare_month
  seed.sql          datos de ejemplo 100% SQL (12 aeropuertos, 8 rutas, 1.448 vuelos, ~150k reservas)
  app/db.py         pool psycopg_pool (DB_*, sslmode=require, pool 4-20 por worker)
  app/main.py       endpoints FastAPI
  Dockerfile, requirements.txt, .env.example
docker-compose.yml  servicio api (puerto 8000, 2 workers)
tests/              pytest contra BASE_URL (incluye prueba de concurrencia)
infra/glue/etl_oltp_to_olap.py   job PySpark Glue 4.0
```

## Ejecución (real, todo en AWS)
Nada corre en local. Las ejecuciones se hacen en la EC2 `airline-api` vía SSM (`infra/ssm_run.sh "<cmd>"`).
1. `infra/1_red_y_rds.sh` (SGs, SSM params, RDS `airline-pg`, bucket) e `infra/3_ec2_api.sh` (EC2 t3.small AL2023 con docker y psql 16).
2. Empaquetar y subir: `zip -r airline-system.zip . -x '.git/*' '*.env'` y `aws s3 cp airline-system.zip s3://airline-system-930581488240/code/`.
   En la EC2: `aws s3 cp s3://.../code/airline-system.zip /tmp/a.zip && unzip -o /tmp/a.zip -d /opt/airline-system`.
3. Bases, esquema y datos (en la EC2): `bash /opt/airline-system/infra/2_esquemas_y_datos.sh`
   (crea airline_oltp/airline_olap, aplica schema.sql, seed.sql ~20 s, schema_olap.sql). Resultado: 1.448 vuelos, 150.197 reservas, 170.135 tramos, inconsistencias = 0.
4. API (en la EC2): `bash /opt/airline-system/backend/run_api.sh --build` (docker build + run; lee DB_* de SSM; pool 4-20 por worker, 2 workers).
   `curl http://<IP>:8000/health`. `infra/encender.sh` reinicia RDS y EC2 y relanza la API, imprimiendo la nueva IP.
5. Pruebas (en la EC2): `python3 -m venv /opt/venv && /opt/venv/bin/pip install -r tests/requirements.txt && BASE_URL=http://localhost:8000 /opt/venv/bin/pytest -v -s tests/ | tee evidencia_pruebas.txt`
   y `BASE_URL=... python tests/p95_search.py` (p95 de búsqueda, 100 peticiones, 20 clientes). Evidencias en `evidence/` y `s3://.../evidence/`.
6. ETL: subir `infra/glue/etl_oltp_to_olap.py` a `s3://airline-system-930581488240/glue/` y ejecutar el job `airline-etl-oltp-to-olap`.

## Endpoints
- `GET /health`
- `GET /flights/search?origin=&destination=&date=` (solo vuelos con sillas)
- `GET /flights/{flight_id}` (extra, para verificar sillas)
- `POST /reservations` `{flight_id, passenger_name, fare_class, connection_flight_ids?, document_id?, email?}` → 201 / 404 / 409 / 422
- `GET /reservations/{id}` → 200 / 404
- `POST /reservations/{id}/cancel` → 200 / 404 / 409
- `POST /admin/flights` `{flight_id, route:"AAA-BBB", flight_date, capacity, price_cents, ...}` → 201 / 409

## Concurrencia
Una transacción por reserva; `UPDATE flights SET seats_available = seats_available - 1 WHERE flight_id=%s AND seats_available>0`
en orden ascendente de `flight_id`. Si algún tramo no tiene sillas se hace ROLLBACK y se responde 409 (todo o nada).

## Glue (ETL + Data Catalog) — `infra/4_glue.sh` (idempotente)
- Conexiones JDBC `airline-oltp-conn` / `airline-olap-conn` (RDS `airline-pg`, subnet/AZ real de la instancia: us-east-1a, SG `airline-db-sg`); credenciales leídas de SSM al ejecutar. Sin NAT: Glue llega a S3 por el VPC endpoint Gateway.
- Data Catalog: bases `airline_oltp` (10 tablas) y `airline_olap` (2 tablas) pobladas por los crawlers `airline-oltp-crawler` y `airline-olap-crawler` (JDBC `<db>/public/%`, LabRole, solo bajo demanda, sin schedule).
- Job `airline-etl-oltp-to-olap` (Glue 4.0, 2 × G.1X, LabRole, timeout 15 min, 0 reintentos): script en `s3://airline-system-930581488240/glue/etl_oltp_to_olap.py`; trunca y recarga `fact_route_month` (63 filas) y `fact_fare_month` (23 filas). Corrida de referencia: ~110 s, 220 DPU-s.
- Evidencias: `evidence/glue_evidencia.txt` (también en `s3://.../evidence/`).
