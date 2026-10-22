-- Migración check-in / clases tarifarias / vuelos internacionales. Transaccional e idempotente.
-- Aplicar sobre airline_oltp:  psql -v ON_ERROR_STOP=1 -f migracion_checkin.sql
BEGIN;

-- 1. Aeropuertos y rutas: tipo de ruta
ALTER TABLE airports ADD COLUMN IF NOT EXISTS country CHAR(2) NOT NULL DEFAULT 'CO';
ALTER TABLE routes   ADD COLUMN IF NOT EXISTS route_type TEXT NOT NULL DEFAULT 'NACIONAL';
ALTER TABLE routes DROP CONSTRAINT IF EXISTS routes_route_type_check;
ALTER TABLE routes ADD CONSTRAINT routes_route_type_check CHECK (route_type IN ('NACIONAL','INTERNACIONAL'));

-- 2. Tarifas: cabina, silla en la compra, multiplicador con 3 decimales
ALTER TABLE fare_rules ADD COLUMN IF NOT EXISTS cabin TEXT NOT NULL DEFAULT 'ECONOMICA';
ALTER TABLE fare_rules ADD COLUMN IF NOT EXISTS seat_at_purchase BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE fare_rules ALTER COLUMN multiplier TYPE NUMERIC(6,3);
ALTER TABLE fare_rules DROP CONSTRAINT IF EXISTS fare_rules_fare_class_check;
ALTER TABLE fare_rules DROP CONSTRAINT IF EXISTS fare_rules_cabin_check;

-- Tramos de días (los mismos de ECONOMY) x multiplicador de la clase
INSERT INTO fare_rules (fare_class, min_days_before, multiplier, cabin, seat_at_purchase)
SELECT c.fare_class, t.min_days_before, round(t.mult * c.mult, 3), c.cabin, c.seat_at_purchase
FROM (VALUES (21, 1.00), (7, 1.30), (0, 1.70)) AS t(min_days_before, mult)
CROSS JOIN (VALUES ('XS', 0.70, 'ECONOMICA', false),
                   ('S',  0.85, 'ECONOMICA', false),
                   ('M',  1.00, 'ECONOMICA', false),
                   ('L',  1.30, 'ECONOMICA', true),
                   ('PR', 2.20, 'PREMIUM',   true)) AS c(fare_class, mult, cabin, seat_at_purchase)
ON CONFLICT (fare_class, min_days_before)
DO UPDATE SET multiplier = EXCLUDED.multiplier, cabin = EXCLUDED.cabin, seat_at_purchase = EXCLUDED.seat_at_purchase;

-- Reservas existentes: ECONOMY -> M, BUSINESS -> PR (mismo tramo de días)
UPDATE reservations r SET fare_rule_id = n.fare_rule_id
FROM fare_rules o
JOIN fare_rules n ON n.min_days_before = o.min_days_before
                 AND n.fare_class = CASE o.fare_class WHEN 'ECONOMY' THEN 'M' ELSE 'PR' END
WHERE r.fare_rule_id = o.fare_rule_id AND o.fare_class IN ('ECONOMY','BUSINESS');
DELETE FROM fare_rules WHERE fare_class IN ('ECONOMY','BUSINESS');

ALTER TABLE fare_rules ADD CONSTRAINT fare_rules_fare_class_check CHECK (fare_class IN ('XS','S','M','L','PR'));
ALTER TABLE fare_rules ADD CONSTRAINT fare_rules_cabin_check CHECK (cabin IN ('ECONOMICA','PREMIUM'));

-- 3. Sillas, bloqueos y check-in por tramo
CREATE TABLE IF NOT EXISTS seat_assignments (
    flight_id      TEXT NOT NULL REFERENCES flights(flight_id),
    seat_no        TEXT NOT NULL,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    assigned_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (flight_id, seat_no),
    UNIQUE (reservation_id, flight_id)
);
CREATE TABLE IF NOT EXISTS seat_holds (
    flight_id      TEXT NOT NULL REFERENCES flights(flight_id),
    seat_no        TEXT NOT NULL,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    expires_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (flight_id, seat_no)
);
CREATE INDEX IF NOT EXISTS idx_seat_holds_res ON seat_holds (reservation_id);
ALTER TABLE reservation_segments ADD COLUMN IF NOT EXISTS checked_in_at TIMESTAMPTZ NULL;

-- 4. Vuelos internacionales (mismo patrón y rango de fechas del seed)
INSERT INTO airports (code, city, country) VALUES
 ('MIA','Miami','US'),('PTY','Ciudad de Panamá','PA'),('LIM','Lima','PE'),('UIO','Quito','EC')
ON CONFLICT (code) DO NOTHING;

INSERT INTO routes (route_code, origin, destination, route_type) VALUES
 ('BOG-MIA','BOG','MIA','INTERNACIONAL'),('MIA-BOG','MIA','BOG','INTERNACIONAL'),
 ('BOG-PTY','BOG','PTY','INTERNACIONAL'),('PTY-BOG','PTY','BOG','INTERNACIONAL')
ON CONFLICT (route_code) DO UPDATE SET route_type = 'INTERNACIONAL';

INSERT INTO aircraft (aircraft_id, model, capacity) VALUES
 ('AC-09','Airbus A320',150),('AC-10','Airbus A320',150),('AC-11','Airbus A320',150),('AC-12','Airbus A320',150)
ON CONFLICT (aircraft_id) DO NOTHING;

INSERT INTO scheduled_flights (flight_number, route_code, departure_time, days_of_week, aircraft_id) VALUES
 ('AV109','BOG-MIA','07:15','1234567','AC-09'),('AV110','MIA-BOG','12:30','1234567','AC-10'),
 ('AV111','BOG-PTY','09:40','1234567','AC-11'),('AV112','PTY-BOG','14:20','1234567','AC-12')
ON CONFLICT (flight_number) DO NOTHING;

INSERT INTO flights (flight_id, flight_number, route_code, aircraft_id, flight_date, departure_time,
                     capacity, seats_available, price_cents)
SELECT s.flight_number || '-' || to_char(d, 'YYYYMMDD'), s.flight_number, s.route_code, s.aircraft_id,
       d::date, s.departure_time, 150, 150,
       (round((CASE s.route_code WHEN 'BOG-MIA' THEN 1450000 WHEN 'MIA-BOG' THEN 1430000
                                 WHEN 'BOG-PTY' THEN 980000 ELSE 970000 END
               * (0.9 + (abs(hashtext(s.flight_number || to_char(d, 'YYYYMMDD'))) % 21) / 100.0)) / 1000) * 1000 * 100)::bigint
FROM scheduled_flights s
CROSS JOIN generate_series(date '2026-06-01', date '2026-11-28', interval '1 day') d
WHERE s.flight_number IN ('AV109','AV110','AV111','AV112')
ON CONFLICT (flight_id) DO NOTHING;

-- Rutas nacionales adicionales (20, ida y vuelta): conectan los 12 aeropuertos. Sin reservas en el seed.
CREATE TEMP TABLE IF NOT EXISTS nac_new (k int, route_code text, origin char(3), destination char(3), base int) ON COMMIT DROP;
INSERT INTO nac_new
SELECT row_number() OVER (ORDER BY o, d, dir), o || '-' || d, CASE WHEN dir = 0 THEN o ELSE d END, CASE WHEN dir = 0 THEN d ELSE o END, base
FROM (VALUES ('BOG','BGA',260000),('BOG','BAQ',330000),('BOG','SMR',360000),('BOG','PEI',250000),
             ('BOG','CUC',340000),('BOG','ADZ',520000),('BOG','LET',560000),('BOG','PSO',380000),
             ('MDE','BGA',270000),('MDE','BAQ',310000)) AS p(o, d, base)
CROSS JOIN (VALUES (0),(1)) AS x(dir);
UPDATE nac_new SET route_code = origin || '-' || destination;

INSERT INTO routes (route_code, origin, destination, route_type)
SELECT route_code, origin, destination, 'NACIONAL' FROM nac_new ON CONFLICT (route_code) DO NOTHING;
INSERT INTO aircraft (aircraft_id, model, capacity)
SELECT 'AC-' || (12 + k), 'Airbus A320', 150 FROM nac_new ON CONFLICT (aircraft_id) DO NOTHING;
INSERT INTO scheduled_flights (flight_number, route_code, departure_time, days_of_week, aircraft_id)
SELECT 'AV' || (112 + k), route_code, time '05:30' + ((k * 47) % 960) * interval '1 minute', '1234567', 'AC-' || (12 + k)
FROM nac_new ON CONFLICT (flight_number) DO NOTHING;
INSERT INTO flights (flight_id, flight_number, route_code, aircraft_id, flight_date, departure_time,
                     capacity, seats_available, price_cents)
SELECT s.flight_number || '-' || to_char(d, 'YYYYMMDD'), s.flight_number, s.route_code, s.aircraft_id,
       d::date, s.departure_time, 150, 150,
       (round((n.base * (0.9 + (abs(hashtext(s.flight_number || to_char(d, 'YYYYMMDD'))) % 21) / 100.0)) / 1000) * 1000 * 100)::bigint
FROM nac_new n
JOIN scheduled_flights s ON s.route_code = n.route_code
CROSS JOIN generate_series(date '2026-06-01', date '2026-11-28', interval '1 day') d
ON CONFLICT (flight_id) DO NOTHING;

COMMIT;
