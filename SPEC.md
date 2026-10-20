# SPEC — Sistema de Reservas Aéreas (Parcial Big Data, USA)

> ACTUALIZACIÓN (compatibilidad con parcial 2, harness en ~/Descargas/parcial2-harness-estudiantes (3)):
> - DB = RDS PostgreSQL 16 `airline-pg` db.t3.micro single-AZ (Aurora prohibido por R11).
> - flights.flight_id TEXT (lo da el cliente). 12 aeropuertos: BOG MDE CLO CTG BAQ SMR BGA PEI CUC ADZ LET PSO.
> - POST /admin/flights {flight_id, route, flight_date, capacity, price_cents} → 201 / 409; crea ruta si no existe.
> - POST /reservations {flight_id, passenger_name, fare_class, connection_flight_ids?} → 201 {reservation_id, flight_id, status, passenger_name, ...}.
> - Search devuelve {flight_id, route, flight_date, seats_available, price_cents}.
> - Solo se construye el parcial 1 (sin EMR/Athena/frontend).

Fuente de verdad compartida por todos los agentes. Regla: NADA se ejecuta en local
(no docker, no pytest, no psql local). Los archivos se escriben en ~/airline-system/,
se suben a S3 y se ejecutan en AWS (EC2 vía SSM Run Command, Glue).

## AWS
- Cuenta 930581488240 (Learner Lab, rol voclabs), región us-east-1, credenciales en ~/.aws (default).
- VPC default vpc-055e559fe133bbe1e. Subnets: us-east-1a subnet-04cc25c5f1dc63df5, us-east-1b subnet-019f193722650f8a8, us-east-1c subnet-09e5318c07ecfddce.
- IP del equipo: 181.32.203.201/32.
- IAM: usar LabRole (Glue) y LabInstanceProfile (EC2). No crear roles.
- Prefijo de recursos: `airline-`. Tag en todo: Project=airline-system.

| Recurso | Nombre |
|---|---|
| SG base de datos | airline-db-sg (5432 desde 181.32.203.201/32, desde airline-api-sg, y autorreferencia all-TCP para Glue) |
| SG API | airline-api-sg (8000 y 22 desde 181.32.203.201/32) |
| DB subnet group | airline-db-subnets (subnets a,b,c) |
| Aurora cluster | airline-aurora (aurora-postgresql 16.x, master user `airline_admin`, StorageEncrypted=true) |
| Aurora instancia | airline-aurora-1 (db.t3.medium, PubliclyAccessible=true pero SG restringido) |
| Fallback si Aurora no se permite | RDS PostgreSQL 16 `airline-pg`, db.t3.micro, cifrado |
| SSM params | /airline/db/password (SecureString), /airline/db/host, /airline/db/user |
| Bucket S3 | airline-system-930581488240 (código en code/, glue script en glue/, evidencias en evidence/, backups en backup/) |
| Endpoint S3 | Gateway en la VPC default (route tables de la VPC) |
| EC2 | airline-api, t3.small, Amazon Linux 2023, gp3 8GB, LabInstanceProfile, SG airline-api-sg |
| Bases | airline_oltp (transaccional), airline_olap (analítica), mismo cluster |
| Glue connections | airline-oltp-conn, airline-olap-conn (JDBC, subnet us-east-1a, SG airline-db-sg) |
| Glue crawlers | airline-oltp-crawler → DB catálogo `airline_oltp`; airline-olap-crawler → `airline_olap` |
| Glue job | airline-etl-oltp-to-olap (Glue 4.0, 2 × G.1X, LabRole) |

## Modelo transaccional (airline_oltp, esquema public) — montos BIGINT en centavos COP
- airports(code CHAR(3) PK, city TEXT NOT NULL)
- routes(route_code TEXT PK, origin CHAR(3) FK airports, destination CHAR(3) FK airports, CHECK origin<>destination)
- aircraft(aircraft_id TEXT PK, model TEXT, capacity INT CHECK >0)
- scheduled_flights(flight_number TEXT PK, route_code FK, departure_time TIME, days_of_week TEXT, aircraft_id FK)
- flights(flight_id BIGSERIAL PK, flight_number FK NULL, route_code FK NOT NULL, aircraft_id FK NULL, flight_date DATE, departure_time TIME, capacity INT, seats_available INT, price_cents BIGINT, CHECK (seats_available BETWEEN 0 AND capacity), UNIQUE(flight_number, flight_date), INDEX (route_code, flight_date))
- passengers(passenger_id BIGSERIAL PK, full_name, document_id, email)
- fare_rules(fare_rule_id SERIAL PK, fare_class TEXT CHECK IN ('ECONOMY','BUSINESS'), min_days_before INT, multiplier NUMERIC(4,2), UNIQUE(fare_class,min_days_before)) — 6 reglas: ECONOMY 21→1.00, 7→1.30, 0→1.70; BUSINESS 21→2.50, 7→3.00, 0→3.60
- reservations(reservation_id TEXT PK código 6 chars alfanum, passenger_id FK, fare_rule_id FK, status CHECK IN ('CONFIRMED','CANCELLED'), total_price_cents BIGINT, created_at, updated_at TIMESTAMPTZ)
- reservation_segments(reservation_id FK, segment_no SMALLINT, flight_id FK, price_cents BIGINT, PK(reservation_id, segment_no))
- payments(payment_id BIGSERIAL PK, reservation_id UNIQUE FK, amount_cents, provider TEXT DEFAULT 'SIMULADO', provider_ref TEXT, status CHECK IN ('APPROVED','REFUNDED'), created_at, updated_at)

## Modelo analítico (airline_olap)
- fact_route_month(route_code, month DATE, seats_capacity, seats_sold, occupancy NUMERIC, revenue_cents, bookings, cancellations, etl_loaded_at, PK(route_code, month))
- fact_fare_month(fare_class, month DATE, revenue_cents, bookings, cancellations, cancel_rate NUMERIC, etl_loaded_at, PK(fare_class, month))
- Conteo por tramo. Revenue solo de CONFIRMED. Sin datos personales.

## API (FastAPI, puerto 8000, Uvicorn 2 workers, psycopg 3 + psycopg_pool)
- GET /health → 200 {"status":"ok","db":"ok"}
- GET /flights/search?origin=BOG&destination=MDE&date=YYYY-MM-DD → 200 lista con seats_available>0; 422 parámetros inválidos
- POST /reservations body {passenger:{full_name,document_id,email}, flight_id, connection_flight_ids:[...] (0..3), fare_class} → 201 con reservation_id, segments, total, payment; 404 vuelo inexistente; 409 sin sillas; 422 tramos que no encadenan / vuelo pasado / >4 tramos
- GET /reservations/{id} → 200 | 404
- POST /reservations/{id}/cancel → 200 | 404 | 409 ya cancelada
- POST /admin/flights body {route_code, flight_date, capacity, price_cents, flight_number?, aircraft_id?, departure_time?} → 201 | 409 duplicado

## Concurrencia (núcleo)
Dentro de UNA transacción: validar encadenamiento (destino tramo i = origen tramo i+1, fechas no retroceden), luego para cada flight_id en orden ASCENDENTE:
`UPDATE flights SET seats_available = seats_available - 1 WHERE flight_id=%s AND seats_available>0 AND flight_date >= CURRENT_DATE RETURNING ...`
Si 0 filas → ROLLBACK y 409. Precio = price_cents × multiplier de la regla con mayor min_days_before ≤ días que faltan (calculado con la fecha del primer tramo). Luego INSERT passenger, reservation, segments, payment APPROVED. Cancel: UPDATE reservation status con WHERE status='CONFIRMED', +1 silla por tramo (orden ascendente), payment REFUNDED.

## Conexión DB
Variables: DB_HOST, DB_PORT=5432, DB_USER, DB_PASSWORD, DB_NAME=airline_oltp, sslmode=require. En EC2 se leen de SSM.

## Datos de ejemplo
8 rutas (BOG-MDE, MDE-BOG, BOG-CLO, CLO-BOG, BOG-CTG, CTG-BOG, MDE-CTG, CTG-MDE), aeropuertos BOG MDE CLO CTG, 1 vuelo diario de 150 sillas por ruta, ~181 días (2026-06-01 → 2026-11-28) ≈ 1.448 vuelos, ~160k reservas (≈ 10-15% canceladas, mezcla ECONOMY 85% / BUSINESS 15%, algunas con escala). seats_available debe ser consistente con los tramos CONFIRMED. Generación por SQL (generate_series) o COPY para que cargue en minutos.
