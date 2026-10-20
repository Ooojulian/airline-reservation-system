-- Esquema analítico airline_olap (la base debe existir; se crea aparte: CREATE DATABASE airline_olap).
-- Conteo por tramo. Revenue solo de CONFIRMED. Sin datos personales.

DROP TABLE IF EXISTS fact_route_month CASCADE;
DROP TABLE IF EXISTS fact_fare_month  CASCADE;

CREATE TABLE fact_route_month (
    route_code     TEXT          NOT NULL,
    month          DATE          NOT NULL,   -- primer día del mes del vuelo
    seats_capacity BIGINT        NOT NULL,
    seats_sold     BIGINT        NOT NULL,   -- tramos CONFIRMED
    occupancy      NUMERIC(7,4)  NOT NULL,   -- seats_sold / seats_capacity
    revenue_cents  BIGINT        NOT NULL,
    bookings       BIGINT        NOT NULL,   -- tramos CONFIRMED
    cancellations  BIGINT        NOT NULL,   -- tramos CANCELLED
    etl_loaded_at  TIMESTAMP     NOT NULL DEFAULT now(),
    PRIMARY KEY (route_code, month)
);

CREATE TABLE fact_fare_month (
    fare_class    TEXT          NOT NULL,
    month         DATE          NOT NULL,
    revenue_cents BIGINT        NOT NULL,
    bookings      BIGINT        NOT NULL,
    cancellations BIGINT        NOT NULL,
    cancel_rate   NUMERIC(7,4)  NOT NULL,
    etl_loaded_at TIMESTAMP     NOT NULL DEFAULT now(),
    PRIMARY KEY (fare_class, month)
);
