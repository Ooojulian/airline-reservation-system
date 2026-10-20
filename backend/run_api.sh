#!/usr/bin/env bash
# Se ejecuta EN LA EC2: construye (opcional) y relanza el contenedor leyendo credenciales de SSM.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
cd /opt/airline-system/backend
[ "${1:-}" = "--build" ] && docker build -t airline-api .
docker rm -f airline-api 2>/dev/null || true
docker run -d --name airline-api --restart unless-stopped -p 8000:8000 \
  -e DB_HOST="$(aws ssm get-parameter --name /airline/db/host --query Parameter.Value --output text)" \
  -e DB_USER="$(aws ssm get-parameter --name /airline/db/user --query Parameter.Value --output text)" \
  -e DB_PASSWORD="$(aws ssm get-parameter --name /airline/db/password --with-decryption --query Parameter.Value --output text)" \
  -e DB_NAME=airline_oltp -e DB_PORT=5432 -e DB_POOL_MIN=4 -e DB_POOL_MAX=20 -e DB_POOL_TIMEOUT=120 \
  airline-api
for i in $(seq 1 30); do curl -sf localhost:8000/health && echo && exit 0; sleep 2; done
echo "API no responde"; docker logs --tail 30 airline-api; exit 1
