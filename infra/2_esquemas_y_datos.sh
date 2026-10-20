#!/usr/bin/env bash
# Se ejecuta EN LA EC2 (vía SSM). Crea bases y aplica esquemas/seed. Credenciales desde SSM.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
DB_HOST=$(aws ssm get-parameter --name /airline/db/host --query Parameter.Value --output text)
DB_USER=$(aws ssm get-parameter --name /airline/db/user --query Parameter.Value --output text)
export PGPASSWORD=$(aws ssm get-parameter --name /airline/db/password --with-decryption --query Parameter.Value --output text)
export PGSSLMODE=require
cd /opt/airline-system/backend
P="psql -h $DB_HOST -U $DB_USER -v ON_ERROR_STOP=1 -X"
for db in airline_oltp airline_olap; do
  $P -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || $P -d postgres -c "CREATE DATABASE $db"
done
$P -d airline_oltp -q -f schema.sql
time $P -d airline_oltp -f seed.sql
$P -d airline_olap -q -f schema_olap.sql
$P -d airline_oltp -c "SELECT (SELECT count(*) FROM flights) vuelos,(SELECT count(*) FROM reservations) reservas,(SELECT count(*) FROM reservation_segments) tramos,(SELECT count(*) FROM payments) pagos,(SELECT count(*) FROM passengers) pasajeros"
