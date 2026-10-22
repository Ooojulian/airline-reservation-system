-- Esquema transaccional airline_oltp (PostgreSQL 16 / Aurora). Montos en centavos COP (BIGINT).
-- Idempotente: se puede ejecutar varias veces (borra y recrea todo).

DROP TABLE IF EXISTS seat_holds           CASCADE;
DROP TABLE IF EXISTS seat_assignments     CASCADE;
DROP TABLE IF EXISTS payments             CASCADE;
DROP TABLE IF EXISTS reservation_segments CASCADE;
DROP TABLE IF EXISTS reservations         CASCADE;
DROP TABLE IF EXISTS fare_rules           CASCADE;
DROP TABLE IF EXISTS passengers           CASCADE;
DROP TABLE IF EXISTS flights              CASCADE;
DROP TABLE IF EXISTS scheduled_flights    CASCADE;
DROP TABLE IF EXISTS aircraft             CASCADE;
DROP TABLE IF EXISTS routes               CASCADE;
DROP TABLE IF EXISTS airports             CASCADE;

CREATE TABLE airports (
    code CHAR(3) PRIMARY KEY,
    city TEXT NOT NULL,
    country CHAR(2) NOT NULL DEFAULT 'CO'
);

CREATE TABLE routes (
    route_code  TEXT PRIMARY KEY,
    origin      CHAR(3) NOT NULL REFERENCES airports(code),
    destination CHAR(3) NOT NULL REFERENCES airports(code),
    route_type  TEXT NOT NULL DEFAULT 'NACIONAL' CONSTRAINT routes_route_type_check CHECK (route_type IN ('NACIONAL','INTERNACIONAL')),
    CHECK (origin <> destination)
);

CREATE TABLE aircraft (
    aircraft_id TEXT PRIMARY KEY,
    model       TEXT,
    capacity    INT CHECK (capacity > 0)
);

CREATE TABLE scheduled_flights (
    flight_number  TEXT PRIMARY KEY,
    route_code     TEXT NOT NULL REFERENCES routes(route_code),
    departure_time TIME,
    days_of_week   TEXT,            -- '1234567' = lunes..domingo
    aircraft_id    TEXT REFERENCES aircraft(aircraft_id)
);

CREATE TABLE flights (
    flight_id       TEXT PRIMARY KEY,           -- lo asigna el cliente, ej. 'AV101-20260601'
    flight_number   TEXT NULL REFERENCES scheduled_flights(flight_number),
    route_code      TEXT NOT NULL REFERENCES routes(route_code),
    aircraft_id     TEXT NULL REFERENCES aircraft(aircraft_id),
    flight_date     DATE NOT NULL,
    departure_time  TIME,
    capacity        INT NOT NULL,
    seats_available INT NOT NULL,
    price_cents     BIGINT NOT NULL,
    CONSTRAINT flights_seats_chk CHECK (seats_available BETWEEN 0 AND capacity),
    CONSTRAINT flights_number_date_uk UNIQUE (flight_number, flight_date)
);
CREATE INDEX idx_flights_route_date ON flights (route_code, flight_date);

CREATE TABLE passengers (
    passenger_id BIGSERIAL PRIMARY KEY,
    full_name    TEXT NOT NULL,
    document_id  TEXT,
    email        TEXT
);

CREATE TABLE fare_rules (
    fare_rule_id   SERIAL PRIMARY KEY,
    fare_class     TEXT NOT NULL CHECK (fare_class IN ('XS','S','M','L','PR')),
    min_days_before INT NOT NULL,
    multiplier     NUMERIC(6,3) NOT NULL,
    cabin          TEXT NOT NULL DEFAULT 'ECONOMICA' CONSTRAINT fare_rules_cabin_check CHECK (cabin IN ('ECONOMICA','PREMIUM')),
    seat_at_purchase BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (fare_class, min_days_before)
);

CREATE TABLE reservations (
    reservation_id    TEXT PRIMARY KEY CHECK (char_length(reservation_id) = 6),
    passenger_id      BIGINT NOT NULL REFERENCES passengers(passenger_id),
    fare_rule_id      INT NOT NULL REFERENCES fare_rules(fare_rule_id),
    status            TEXT NOT NULL CHECK (status IN ('CONFIRMED','CANCELLED')),
    total_price_cents BIGINT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_reservations_passenger ON reservations (passenger_id);
CREATE INDEX idx_reservations_status    ON reservations (status);

CREATE TABLE reservation_segments (
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    segment_no     SMALLINT NOT NULL,
    flight_id      TEXT NOT NULL REFERENCES flights(flight_id),
    price_cents    BIGINT NOT NULL,
    checked_in_at  TIMESTAMPTZ NULL,
    PRIMARY KEY (reservation_id, segment_no)
);
CREATE INDEX idx_segments_flight ON reservation_segments (flight_id);

CREATE TABLE payments (
    payment_id     BIGSERIAL PRIMARY KEY,
    reservation_id TEXT NOT NULL UNIQUE REFERENCES reservations(reservation_id),
    amount_cents   BIGINT NOT NULL,
    provider       TEXT NOT NULL DEFAULT 'SIMULADO',
    provider_ref   TEXT,
    status         TEXT NOT NULL CHECK (status IN ('APPROVED','REFUNDED')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sillas: la clave primaria (flight_id, seat_no) garantiza que dos reservas nunca tengan la misma silla.
CREATE TABLE seat_assignments (
    flight_id      TEXT NOT NULL REFERENCES flights(flight_id),
    seat_no        TEXT NOT NULL,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    assigned_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (flight_id, seat_no),
    UNIQUE (reservation_id, flight_id)
);
-- Bloqueo temporal (5 min) durante el check-in; vencido = libre.
CREATE TABLE seat_holds (
    flight_id      TEXT NOT NULL REFERENCES flights(flight_id),
    seat_no        TEXT NOT NULL,
    reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id),
    expires_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (flight_id, seat_no)
);
CREATE INDEX idx_seat_holds_res ON seat_holds (reservation_id);

-- Reglas tarifarias: se aplica la de mayor min_days_before <= días que faltan al primer tramo.
-- Tramos de días x multiplicador de la clase (XS .70, S .85, M 1.00, L 1.30, PR 2.20).
INSERT INTO fare_rules (fare_class, min_days_before, multiplier, cabin, seat_at_purchase)
SELECT c.fare_class, t.min_days_before, round(t.mult * c.mult, 3), c.cabin, c.seat_at_purchase
FROM (VALUES (21, 1.00), (7, 1.30), (0, 1.70)) AS t(min_days_before, mult)
CROSS JOIN (VALUES ('XS', 0.70, 'ECONOMICA', false),
                   ('S',  0.85, 'ECONOMICA', false),
                   ('M',  1.00, 'ECONOMICA', false),
                   ('L',  1.30, 'ECONOMICA', true),
                   ('PR', 2.20, 'PREMIUM',   true)) AS c(fare_class, mult, cabin, seat_at_purchase);
