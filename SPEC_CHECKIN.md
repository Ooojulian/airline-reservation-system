# SPEC — Clases tarifarias, vuelos nacionales/internacionales, sillas y check-in

Complemento del parcial 1 (documento: RN-08, RF-20 a RF-24, RF-81 a RF-83, SUP-22, SUP-23, SUP-57).
Debe convivir con el contrato del parcial 2 (harness 70/70): NO romper `POST /reservations` con
`fare_class: "ECONOMY"`, la forma de las respuestas, ni R2 (sin sobreventa, sin 5xx).

## Reglas generales
- Nada se ejecuta local salvo `py_compile`, `node --check`, `bash -n`. Todo corre en la EC2 vía
  `bash infra/ssm_run.sh "<cmd>"`. Despliegue: subir archivos a `s3://airline-system-930581488240/code/...`,
  bajarlos en la EC2 a `/opt/airline-system/...` y relanzar (`backend/run_api.sh --build`, `frontend/run_web.sh`).
- Credenciales de la DB solo en SSM `/airline/db/*`. Nunca imprimir secretos. No abrir 5432 a internet.
- No hacer commit ni push (lo hace el orquestador). Código en español, mismo estilo que el existente, comentarios cortos.

## Modelo de negocio (lo que define el documento del parcial 1)
| Clase | Cabina | Silla | Multiplicador de precio |
|---|---|---|---|
| XS | Económica | solo en check-in | 0.70 |
| S  | Económica | solo en check-in | 0.85 |
| M  | Económica | solo en check-in | 1.00 |
| L  | Económica | en la compra (o después, hasta el cierre del check-in) | 1.30 |
| PR | Premium   | en la compra (o después, hasta el cierre del check-in) | 2.20 |

- Alias de compatibilidad: `ECONOMY` → `M`, `BUSINESS` → `PR` (el harness envía `ECONOMY`).
- Rutas `NACIONAL` o `INTERNACIONAL`. Check-in abre 24 h antes de la salida y cierra 1 h antes (nacional)
  o 2 h antes (internacional). Hora de salida = flight_date + departure_time en America/Bogota.
- Mapa de sillas por vuelo, generado a partir de `capacity`: filas de 6 (A–F), numeradas desde 1.
  Las primeras `floor(capacity * 0.08 / 6)` filas son Premium (PR solo elige ahí; las demás clases solo en Económica).
  Si el vuelo no tiene filas Premium, PR puede elegir cualquier silla.
- Bloqueo de silla de 5 minutos durante el check-in (RF-81). La garantía real es la clave primaria
  `(flight_id, seat_no)` de `seat_assignments` (RF-82): dos reservas nunca quedan con la misma silla.
  Bloqueos vencidos se tratan como libres (limpieza perezosa, sin proceso de barrido).

## Base de datos (migración `infra/migracion_checkin.sql`, transaccional e idempotente)
- `routes.route_type TEXT NOT NULL DEFAULT 'NACIONAL' CHECK (route_type IN ('NACIONAL','INTERNACIONAL'))`.
- Aeropuertos internacionales: MIA (Miami), PTY (Ciudad de Panamá), LIM (Lima), UIO (Quito).
  Rutas INTERNACIONAL: BOG-MIA, MIA-BOG, BOG-PTY, PTY-BOG. Vuelos diarios para esas rutas con el mismo
  patrón y rango de fechas que los vuelos del seed (mirar `backend/seed.sql`), aeronave de 150 sillas,
  flight_number con el mismo formato.
- `fare_rules`: permitir clases XS, S, M, L, PR (con los mismos tramos `min_days_before` que hoy tiene ECONOMY,
  aplicando el multiplicador de la clase). Migrar reservas existentes: ECONOMY → M, BUSINESS → PR, y luego
  quitar las reglas viejas. Tabla nueva o columnas para: cabina y si la silla se elige en la compra.
- `seat_assignments(flight_id, seat_no, reservation_id, assigned_at, PRIMARY KEY (flight_id, seat_no),
  UNIQUE (reservation_id, flight_id))`.
- `seat_holds(flight_id, seat_no, reservation_id, expires_at, PRIMARY KEY (flight_id, seat_no))`.
- Check-in por tramo: `reservation_segments.checked_in_at TIMESTAMPTZ NULL`.
- Antes de migrar: snapshot manual de RDS `airline-pg-pre-checkin-<fecha>`.
- Actualizar también `backend/schema.sql` y `backend/seed.sql` para que una instalación nueva quede igual.

## API (backend/app/main.py; puede ir en un módulo nuevo `backend/app/checkin.py` con APIRouter)
- `POST /reservations`: `fare_class` acepta XS|S|M|L|PR|ECONOMY|BUSINESS; campo opcional `seat`.
  - `seat` con XS/S/M → 422 "esta clase elige silla en el check-in".
  - `seat` con L/PR → se asigna en la misma transacción; silla ocupada o bloqueada por otro → 409 y ROLLBACK
    de toda la reserva. Silla fuera de la cabina o inexistente → 422.
  - La respuesta mantiene la forma actual y agrega `fare_class` canónico, `cabin`, `seat` (o null).
- `GET /reservations/{id}`: agrega por tramo `seat`, `checked_in`, `checkin_opens_at`, `checkin_closes_at`,
  `route_type`.
- `GET /flights/search`: agrega `route_type` a cada vuelo (sin quitar campos).
- `GET /flights/{id}/seats`: `{flight_id, rows, letters, premium_rows, seats:[{seat_no, cabin, status: FREE|TAKEN|HELD}]}`.
- `POST /reservations/{id}/seat {seat}`: solo L/PR, antes del cierre del check-in; cambia o asigna silla.
- `POST /reservations/{id}/checkin/hold {seat}`: requiere ventana abierta; bloquea 5 min
  (`INSERT ... ON CONFLICT (flight_id, seat_no) DO UPDATE ... WHERE seat_holds.expires_at < now() OR
  seat_holds.reservation_id = EXCLUDED.reservation_id`); si no se pudo → 409. Libera holds anteriores de la misma reserva.
- `POST /reservations/{id}/checkin {seat?}`: requiere ventana abierta y reserva CONFIRMED.
  XS/S/M: debe venir `seat`; se inserta en `seat_assignments` (violación de PK → 409), se borra el hold.
  L/PR: si ya tiene silla, `seat` es opcional. Marca `checked_in_at`. Devuelve pase de abordar
  `{reservation_id, passenger_name, flight_id, flight_number, route, departure, seat, cabin, fare_class, boarding_group}`
  (grupo 1 PR, 2 L, 3 M, 4 S/XS). Ventana cerrada o aún no abierta → 409 con mensaje claro (abre/cierra a las ...).
  Repetir check-in ya hecho → 200 con el mismo pase.
- `POST /reservations/{id}/cancel`: además libera silla y holds.
- Errores siempre 4xx con `detail` en español; nunca 5xx por conflicto.

## Frontend (frontend/, mismo estilo rojo actual; NO cambiar ni quitar ningún data-testid existente)
- Panel de reserva: selector de tarifa con las 5 clases (nombre, precio calculado, y una línea de condición:
  "Silla en el check-in" / "Elige silla ahora"). Valor por defecto: M (el flujo del harness no debe cambiar).
  Si L o PR: mostrar el mapa de sillas del vuelo para elegir (opcional). El `<select id="fare">` puede
  reemplazarse por botones/radios, pero `confirm-submit` y `passenger-name` siguen igual.
- Resultados: etiqueta "Internacional" cuando `route_type` sea INTERNACIONAL.
- Pase de confirmación: muestra clase y silla, o "Silla: la eliges en el check-in, que abre 24 h antes".
- Página nueva `/checkin` (nginx: `location = /checkin`): escribir código de reserva → estado de la ventana
  (abierta, abre el..., cerrada) → mapa de sillas (Premium arriba, sillas tomadas en gris, la propia resaltada)
  → al tocar una silla se bloquea 5 min con cuenta regresiva visible → "Confirmar check-in" → pase de abordar
  con silla y grupo. Si 409: refrescar el mapa y marcar la silla en disputa (RF-83).
- Enlace "Check-in" en la barra superior de las tres páginas. Responsive y accesible (teclado).

## Pruebas (tests/test_checkin.py, corren en la EC2 contra http://localhost:8000)
Crear vuelos de prueba con `POST /admin/flights` (fechas futuras >= 2027 para que el script de limpieza los borre;
para probar la ventana abierta usar un vuelo creado con fecha de mañana y luego borrarlo explícitamente).
- XS con `seat` → 422. PR con `seat` → 201 y silla asignada; otra PR a la misma silla → 409.
- Check-in fuera de ventana → 409. Dentro de ventana: 20 clientes simultáneos hacen hold de la misma silla →
  exactamente 1 éxito; el resto 409. Check-in completo → pase con silla.
- ECONOMY sigue funcionando (alias de M). Cancelar libera la silla.
- Re-correr `tests/test_api.py` (deben seguir pasando).
