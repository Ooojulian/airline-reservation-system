-- Datos de ejemplo 100% SQL. Ejecutar después de schema.sql:  psql -f seed.sql
-- Reproducible: setseed + cantidades derivadas de hash(flight_id).
-- Resultado: 12 aeropuertos, 8 rutas, 1.448 vuelos (2026-06-01 a 2026-11-28, 150 sillas),
-- ~150k reservas (~12% canceladas, 85% ECONOMY / 15% BUSINESS, ~20k con escala).

BEGIN;
SELECT setseed(0.2026);

-- Limpieza (conserva fare_rules de schema.sql)
TRUNCATE payments, reservation_segments, reservations, passengers, flights,
         scheduled_flights, aircraft, routes, airports RESTART IDENTITY CASCADE;

INSERT INTO airports VALUES ('BOG','Bogotá'),('MDE','Medellín'),('CLO','Cali'),('CTG','Cartagena'),
 ('BAQ','Barranquilla'),('SMR','Santa Marta'),('BGA','Bucaramanga'),('PEI','Pereira'),
 ('CUC','Cúcuta'),('ADZ','San Andrés'),('LET','Leticia'),('PSO','Pasto');

INSERT INTO routes (route_code, origin, destination) VALUES
 ('BOG-MDE','BOG','MDE'),('MDE-BOG','MDE','BOG'),
 ('BOG-CLO','BOG','CLO'),('CLO-BOG','CLO','BOG'),
 ('BOG-CTG','BOG','CTG'),('CTG-BOG','CTG','BOG'),
 ('MDE-CTG','MDE','CTG'),('CTG-MDE','CTG','MDE');

-- Una aeronave de 150 sillas por ruta
INSERT INTO aircraft (aircraft_id, model, capacity)
SELECT 'AC-' || lpad(row_number() OVER (ORDER BY route_code)::text, 2, '0'), 'Airbus A320', 150
FROM routes;

INSERT INTO scheduled_flights (flight_number, route_code, departure_time, days_of_week, aircraft_id)
SELECT 'AV' || (100 + row_number() OVER (ORDER BY route_code))::text,
       route_code,
       time '06:00' + (row_number() OVER (ORDER BY route_code)) * interval '90 minutes',
       '1234567',
       'AC-' || lpad((row_number() OVER (ORDER BY route_code))::text, 2, '0')
FROM routes;

-- Vuelos: 1 diario por ruta (181 días)
INSERT INTO flights (flight_id, flight_number, route_code, aircraft_id, flight_date, departure_time,
                     capacity, seats_available, price_cents)
SELECT s.flight_number || '-' || to_char(d, 'YYYYMMDD'), s.flight_number, s.route_code, s.aircraft_id, d::date, s.departure_time, 150, 150,
       -- precio base ~ 180.000-420.000 COP (en centavos), +-20% aleatorio, redondeado a 1.000 COP
       (round((CASE s.route_code
                 WHEN 'BOG-MDE' THEN 280000 WHEN 'MDE-BOG' THEN 285000
                 WHEN 'BOG-CLO' THEN 300000 WHEN 'CLO-BOG' THEN 305000
                 WHEN 'BOG-CTG' THEN 420000 WHEN 'CTG-BOG' THEN 415000
                 WHEN 'MDE-CTG' THEN 350000 ELSE 345000 END
               * (0.8 + random() * 0.4)) / 1000) * 1000 * 100)::bigint
FROM scheduled_flights s
CROSS JOIN generate_series(date '2026-06-01', date '2026-11-28', interval '1 day') d;

-- Pasajeros
INSERT INTO passengers (full_name, document_id, email)
SELECT 'Pasajero ' || g, (10000000 + g * 7)::text, 'pasajero' || g || '@example.com'
FROM generate_series(1, 60000) g;

-- Codificador base36 de 6 caracteres (biyectivo => códigos únicos sin colisión)
CREATE FUNCTION pg_temp.b36(v bigint) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT string_agg(substr('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                           ((v / (36::bigint ^ i)::bigint) % 36)::int + 1, 1), '' ORDER BY i DESC)
  FROM generate_series(0, 5) i
$$;

-- Etapa 1: qué reservas se crean. Directas: 80-100 por vuelo. Con escala: 9-13 por vuelo
-- inicial en cada par de rutas encadenables el mismo día (cota dura: <=100 + 39 < 150 sillas).
CREATE TEMP TABLE seed_stage AS
WITH raw AS (
    SELECT f.flight_id AS f1, NULL::text AS f2
    FROM flights f
    CROSS JOIN LATERAL generate_series(1, 80 + abs(hashtext(f.flight_id)) % 21) g
    UNION ALL
    SELECT f1.flight_id, f2.flight_id
    FROM routes a
    JOIN routes b  ON a.destination = b.origin AND a.origin <> b.destination
    JOIN flights f1 ON f1.route_code = a.route_code
    JOIN flights f2 ON f2.route_code = b.route_code AND f2.flight_date = f1.flight_date
    CROSS JOIN LATERAL generate_series(1, 9 + abs(hashtext(f1.flight_id)) % 5) g
)
SELECT row_number() OVER ()                                         AS n,
       f1, f2,
       CASE WHEN random() < 0.15 THEN 'BUSINESS' ELSE 'ECONOMY' END AS cls,
       floor(random() * 61)::int                                    AS days_before,
       CASE WHEN random() < 0.12 THEN 'CANCELLED' ELSE 'CONFIRMED' END AS st
FROM raw;

-- Etapa 2: tarifa, precios por tramo, código y fechas
CREATE TEMP TABLE seed_res AS
SELECT s.n,
       pg_temp.b36((s.n * 1000003 + 12345) % 2176782336) AS code,
       1 + (s.n * 7919) % 60000                          AS passenger_id,
       fr.fare_rule_id,
       s.st, s.f1, s.f2,
       round(a.price_cents * fr.multiplier)::bigint      AS p1,
       round(b.price_cents * fr.multiplier)::bigint      AS p2,
       (a.flight_date - s.days_before)::timestamp
         + random() * interval '1 day'                   AS created_at
FROM seed_stage s
JOIN flights a ON a.flight_id = s.f1
LEFT JOIN flights b ON b.flight_id = s.f2
JOIN LATERAL (SELECT fare_rule_id, multiplier FROM fare_rules
              WHERE fare_class = s.cls AND min_days_before <= s.days_before
              ORDER BY min_days_before DESC LIMIT 1) fr ON true;

INSERT INTO reservations (reservation_id, passenger_id, fare_rule_id, status,
                          total_price_cents, created_at, updated_at)
SELECT code, passenger_id, fare_rule_id, st, p1 + COALESCE(p2, 0), created_at,
       CASE WHEN st = 'CANCELLED' THEN created_at + random() * interval '12 hours' ELSE created_at END
FROM seed_res;

INSERT INTO reservation_segments (reservation_id, segment_no, flight_id, price_cents)
SELECT code, 1, f1, p1 FROM seed_res
UNION ALL
SELECT code, 2, f2, p2 FROM seed_res WHERE f2 IS NOT NULL;

INSERT INTO payments (reservation_id, amount_cents, provider, provider_ref, status, created_at, updated_at)
SELECT r.reservation_id, r.total_price_cents, 'SIMULADO', 'SIM-' || r.reservation_id,
       CASE WHEN r.status = 'CANCELLED' THEN 'REFUNDED' ELSE 'APPROVED' END,
       r.created_at, r.updated_at
FROM reservations r;

-- Consistencia: seats_available = capacity - tramos CONFIRMED
UPDATE flights SET seats_available = capacity;
UPDATE flights f
SET seats_available = f.capacity - c.n
FROM (SELECT rs.flight_id, count(*) AS n
      FROM reservation_segments rs
      JOIN reservations r ON r.reservation_id = rs.reservation_id AND r.status = 'CONFIRMED'
      GROUP BY rs.flight_id) c
WHERE c.flight_id = f.flight_id;

DROP TABLE seed_res;
DROP TABLE seed_stage;
COMMIT;

ANALYZE;

-- Resumen
SELECT (SELECT count(*) FROM flights)              AS vuelos,
       (SELECT count(*) FROM reservations)         AS reservas,
       (SELECT count(*) FROM reservation_segments) AS tramos,
       (SELECT count(*) FROM reservations WHERE status = 'CANCELLED') AS canceladas;

-- Verificación: debe devolver 0
SELECT count(*) AS vuelos_inconsistentes
FROM flights f
LEFT JOIN (SELECT rs.flight_id, count(*) AS n
           FROM reservation_segments rs
           JOIN reservations r ON r.reservation_id = rs.reservation_id AND r.status = 'CONFIRMED'
           GROUP BY rs.flight_id) c ON c.flight_id = f.flight_id
WHERE f.seats_available <> f.capacity - COALESCE(c.n, 0);
