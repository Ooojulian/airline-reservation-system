# SPEC P2 — Panel gerencial (Parcial 2 Big Data)

Fuente de verdad para los agentes del parcial 2. El harness oficial está en
`/home/julian/Descargas/parcial2-harness-estudiantes (3)/parcial2-harness/` (NO editar `harness/`, `tests/`,
`thresholds.yaml`). Contratos en su carpeta `contract/`. Leer también `airline_data/oracle.py`.

## Reglas generales
- NADA se ejecuta local (salvo `py_compile`/`bash -n`/leer archivos). Todo corre en AWS: EC2 vía SSM
  (`/home/julian/airline-system/infra/ssm_run.sh "<cmd>"`), EMR, Athena.
- Equipo: Julian Cristancho, Andrés Urrego. **Seed = 2682**. team_id: "cristancho-urrego".
- Código nuevo dentro de `/home/julian/airline-system/` (repo git privado, ya existe; NO hacer commit ni push, lo hace el orquestador).
- Cada agente deja `docs/p2/EXPLICACION_<tema>.md`: qué hizo, por qué, cómo defenderlo en la sustentación
  (en español, sencillo, sin relleno).
- Nunca imprimir secretos. Credenciales DB en SSM `/airline/db/*`.
- Costos: presupuesto total US$50. EMR SIEMPRE con auto-terminate; verificar al final que no quede clúster activo.

## Infra existente (cuenta 930581488240, us-east-1)
- EC2 `airline-api` i-018f40cd1109b7643, t3.small, AL2023, IP pública actual 54.82.184.169 (cambia al reiniciar),
  rol LabInstanceProfile, Docker. Código en /opt/airline-system. API en contenedor `airline-api` puerto 8000,
  relanzar con `bash /opt/airline-system/backend/run_api.sh [--build]`.
- RDS PostgreSQL 16 `airline-pg` db.t3.micro (base airline_oltp con API del P1).
- SG API sg-0b22c99798f6dc60a (8000, 22 desde IPs del equipo). SG DB sg-02ee4ddb48ac84e4d.
- Bucket `s3://airline-system-930581488240` (code/, evidence/, backup/, glue/).
- VPC default vpc-055e559fe133bbe1e, subnet us-east-1a subnet-04cc25c5f1dc63df5. Endpoint Gateway S3 existe. Sin NAT (prohibido).
- Roles EMR: EMR_DefaultRole, EMR_EC2_DefaultRole (instance profile). LabRole para Glue.
- La API (backend/app/main.py) ya cumple el contrato de R1–R3 (POST /admin/flights, search, POST /reservations, GET /reservations/{id}).

## Layout S3 del P2
- `s3://airline-system-930581488240/raw/flights.csv`, `raw/reservations.csv` (datos crudos, seed 2682, 2.000.000 filas)
- `s3://airline-system-930581488240/raw-sample/` (muestra ~5 % para probar)
- `s3://airline-system-930581488240/curated/reservations_curated/month=YYYY-MM/*.parquet`
- `s3://airline-system-930581488240/curated/route_month_metrics/*.parquet`
- `s3://airline-system-930581488240/emr/` (scripts y logs EMR)
- `s3://airline-system-930581488240/athena-results/`

## Datos y reglas (exactas, ver oracle.py)
- C1 route: quitar TODO espacio y upper. C2 status: strip + upper. C3 status ∈ {CONFIRMED, CANCELLED}.
  C4 price_cents no nulo y > 0. C5 dedup reservation_id (las duplicadas son copias exactas).
- `reservations_curated`: columnas en orden reservation_id(string), flight_id(string), route(string),
  fare_class(string, tal cual viene: minúsculas), price_cents(bigint), status(string); partición month(string YYYY-MM
  del flight_date). Join inner con flights limpios. 24 particiones (2023-01..2024-12).
- `route_month_metrics` (Parquet, sin partición): route, month, seats_capacity(bigint = suma capacity de vuelos de la
  ruta en el mes), seats_sold(bigint, CONFIRMED), occupancy(double = seats_sold/seats_capacity), revenue_cents(bigint,
  CONFIRMED), bookings(bigint), cancellations(bigint).
  DECISIÓN: bookings = CONFIRMED (según el contrato DATA.md; el oráculo oracle.py cuenta CONFIRMED + CANCELLED y por eso
  difiere, pero ningún test revisa ese campo; se declara en el documento).

## EMR
- Clúster EMR 7.x (release más reciente disponible), 1 master + 2 core **m4.large** (único tipo permitido),
  roles por defecto, subnet us-east-1a, logs en s3://.../emr/logs/, step spark-submit, `--auto-terminate`.
- Primero corrida con raw-sample, luego corrida completa. Cada corrida debe terminar COMPLETED.
- Los Parquet finales DEBEN escribirse durante una corrida EMR COMPLETED (el harness compara LastModified).
  La última corrida completa sobrescribe curated/ entero.
- Límite: 150 horas normalizadas (3×m4.large 30 min ≈ 6).

## Glue Data Catalog / Athena
- Base `airline_analytics`. Tablas `reservations_curated` (EXTERNAL, Parquet, PARTITIONED BY month string,
  partition projection: projection.month.type=date, format=yyyy-MM, range=2023-01,2024-12, interval 1 MONTHS,
  storage.location.template con month=${month}) y `route_month_metrics` (Parquet).
- Workgroup `primary`, salida s3://.../athena-results/.

## Endpoint analítico
- `GET /analytics/occupancy?route=AAA-BBB&month=YYYY-MM` → 200 {route, month, occupancy(float), seats_sold(int),
  seats_capacity(int)} leyendo route_month_metrics vía Athena (boto3, credenciales del rol de la EC2).
  404 si no hay fila. Validar parámetros (422 si formato malo; usar parámetros/escape seguro, evitar SQL injection).
  Respuesta ≤ 10 s. Caché en memoria permitido solo después de la primera consulta Athena por (route, month).
- CORS habilitado para el frontend.

## Frontend
- Estático (HTML+JS, sin build) servido por nginx en contenedor `airline-web`, puerto 3000 en la misma EC2.
  nginx hace proxy `/api/` → API :8000 (así no hay problemas de CORS ni de IP).
- `/` cliente: data-testid search-origin, search-destination, search-date (input type=date), search-submit,
  flight-row (uno por resultado, con reserve-button dentro), passenger-name (aparece tras reservar), confirm-submit,
  confirmation-id (texto EXACTO = reservation_id). fare_class ECONOMY por defecto.
- `/manager`: manager-route, manager-month (YYYY-MM), manager-load, occupancy-value (ej. "78.4%", ≥1 decimal;
  usar 3 decimales en % para cumplir tolerancia 0.001: p.ej. 78.432%).
- SG: abrir 3000 a las IPs del equipo (181.32.203.201/32, 192.144.114.66/32).

## Harness
- `config.yaml` en el repo: seed 2682, URLs de la EC2, athena_database airline_analytics, athena_output s3://.../athena-results/.
- `cost_projection.yaml` en el repo (sin total; servicios facturables).
- Se corre en la EC2 (Python + playwright chromium), salida report.md/report.json → evidence/p2/.
