-- DDL del catalogo (Glue Data Catalog via Athena). Las sentencias se separan con una linea que solo contiene ';;'
CREATE DATABASE IF NOT EXISTS airline_analytics
;;
DROP TABLE IF EXISTS airline_analytics.reservations_curated
;;
CREATE EXTERNAL TABLE airline_analytics.reservations_curated (
  reservation_id string,
  flight_id string,
  route string,
  fare_class string,
  price_cents bigint,
  status string
)
PARTITIONED BY (month string)
STORED AS PARQUET
LOCATION 's3://airline-system-930581488240/curated/reservations_curated/'
TBLPROPERTIES (
  'projection.enabled'='true',
  'projection.month.type'='date',
  'projection.month.format'='yyyy-MM',
  'projection.month.range'='2023-01,2024-12',
  'projection.month.interval'='1',
  'projection.month.interval.unit'='MONTHS',
  'storage.location.template'='s3://airline-system-930581488240/curated/reservations_curated/month=${month}/'
)
;;
DROP TABLE IF EXISTS airline_analytics.route_month_metrics
;;
CREATE EXTERNAL TABLE airline_analytics.route_month_metrics (
  route string,
  month string,
  seats_capacity bigint,
  seats_sold bigint,
  occupancy double,
  revenue_cents bigint,
  bookings bigint,
  cancellations bigint
)
STORED AS PARQUET
LOCATION 's3://airline-system-930581488240/curated/route_month_metrics/'
