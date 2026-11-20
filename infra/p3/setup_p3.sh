#!/usr/bin/env bash
# Parcial 3, se corre UNA vez (no cobra por hora): workgroup de Athena con límite de 1 GB por consulta,
# base de datos airline_star en Glue y archivos de referencia (seed) en S3.
# Uso: bash infra/p3/setup_p3.sh <SEED>
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
SEED=${1:?uso: setup_p3.sh <SEED>}
ACC=$(aws sts get-caller-identity --query Account --output text)
B=airline-system-$ACC
HARNESS=${HARNESS:-$HOME/Descargas/Universidad/Big_Data/Parcial_3/harness/parcial3-harness-estudiantes}
REF=$(cd "$(dirname "$0")" && pwd)/ref

# 1) Archivos de referencia de la seed
PY=$HARNESS/.venv/bin/python; [ -x "$PY" ] || PY=python3
(cd "$HARNESS" && $PY -m airline_data.generate_p3 --seed "$SEED" --out "$REF")
aws s3 rm s3://$B/ref/ --recursive --quiet || true
aws s3 cp $REF/routes_ref.csv             s3://$B/ref/routes/routes_ref.csv
aws s3 cp $REF/route_category_history.csv s3://$B/ref/route_history/route_category_history.csv
aws s3 cp $REF/holidays_ref.csv           s3://$B/ref/holidays/holidays_ref.csv

# 2) Workgroup: resultados en S3 y corte de 1 GB por consulta (R10)
CONF="ResultConfiguration={OutputLocation=s3://$B/athena-results/p3/},EnforceWorkGroupConfiguration=true,BytesScannedCutoffPerQuery=1000000000,PublishCloudWatchMetricsEnabled=false"
aws athena create-work-group --name airline-p3 --configuration "$CONF" 2>/dev/null \
  || aws athena update-work-group --work-group airline-p3 --configuration-updates \
     "ResultConfigurationUpdates={OutputLocation=s3://$B/athena-results/p3/},EnforceWorkGroupConfiguration=true,BytesScannedCutoffPerQuery=1000000000"
echo "workgroup airline-p3 listo"

# 3) Base de datos del esquema estrella
aws glue create-database --database-input Name=airline_star 2>/dev/null || echo "airline_star ya existe"
echo "listo: s3://$B/ref/ (seed $SEED)"
