# Clases tarifarias, sillas y check-in: explicación corta

## Qué cambió en la base de datos
- `routes.route_type` (NACIONAL o INTERNACIONAL) y `airports.country`.
- `fare_rules` ahora tiene 5 clases (XS, S, M, L, PR), con columnas `cabin` y `seat_at_purchase`. Los tramos de días son los mismos de antes; el precio final es tramo x multiplicador de la clase (0.70, 0.85, 1.00, 1.30, 2.20). Las reservas viejas pasaron de ECONOMY a M y de BUSINESS a PR.
- `seat_assignments(flight_id, seat_no, reservation_id)` con clave primaria (flight_id, seat_no) y UNIQUE (reservation_id, flight_id).
- `seat_holds` para el bloqueo temporal de 5 minutos.
- `reservation_segments.checked_in_at` marca el check-in por tramo.
- Datos nuevos: 4 aeropuertos internacionales, 4 rutas internacionales y 20 rutas nacionales, con vuelos diarios en el mismo rango de fechas del seed. No tienen reservas.
- La migración está en `infra/migracion_checkin.sql` (transaccional e idempotente). Antes se hizo un snapshot de RDS.

## Qué cambió en la API
- `POST /reservations` acepta XS, S, M, L, PR, y los alias ECONOMY (M) y BUSINESS (PR). Con `seat` solo para L y PR; con XS, S o M responde 422.
- Nuevos: `GET /routes`, `GET /flights/{id}/seats`, `POST /reservations/{id}/seat`, `/checkin/hold` y `/checkin`.
- El cancelar libera la silla y los bloqueos. La búsqueda y la reserva devuelven `route_type`, `cabin` y `seat`.
- El check-in abre 24 h antes y cierra 1 h antes (nacional) o 2 h antes (internacional), en hora de Bogotá. Sin hora de salida se asume 12:00.

## Por qué nunca hay dos reservas con la misma silla
La base de datos no confía en el código de la API. La clave primaria (flight_id, seat_no) de `seat_assignments` hace que un segundo INSERT de la misma silla falle, sin importar cuántas peticiones lleguen a la vez. La API traduce ese error a un 409. Cuando una silla se pide al comprar, el INSERT va en la misma transacción que la reserva: si falla, se deshace todo (no se descuenta cupo ni queda pago).

## Cómo funciona el bloqueo de 5 minutos
Al tocar una silla se hace un `INSERT ... ON CONFLICT DO UPDATE ... WHERE` en `seat_holds`. La fila solo se toma si no existe, si ya venció o si es de la misma reserva. Si 20 clientes piden la misma silla al mismo tiempo, PostgreSQL serializa los INSERT y solo uno gana; los otros reciben 409. No hay proceso de limpieza: un bloqueo vencido se trata como libre. Cada reserva tiene un solo bloqueo por tramo. Al confirmar el check-in el bloqueo se borra y la silla pasa a `seat_assignments`.

## Cómo defenderlo oralmente
1. "La regla de una silla por reserva la garantiza la clave primaria, no el programa."
2. "El bloqueo de 5 minutos es solo comodidad para el usuario; aunque fallara, la clave primaria evita el doble asignado."
3. "La reserva sigue siendo una sola transacción con el UPDATE condicional de cupos, así que no hay sobreventa ni deadlocks."
4. "Los alias ECONOMY y BUSINESS mantienen el contrato de la entrega 2."
5. Evidencia: `tests/test_checkin.py` (20 holds simultáneos, exactamente 1 éxito; 10 check-ins simultáneos sobre la misma silla, exactamente 1 éxito).
