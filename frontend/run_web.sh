#!/usr/bin/env bash
# Se ejecuta EN LA EC2: build + run del frontend. --network host: nginx escucha :3000 y llega a la API en 127.0.0.1:8000.
set -euo pipefail
cd /opt/airline-system/frontend
docker build -t airline-web .
docker rm -f airline-web 2>/dev/null || true
docker run -d --name airline-web --restart unless-stopped --network host airline-web
for i in $(seq 1 15); do curl -sf -o /dev/null localhost:3000/ && echo "airline-web OK :3000" && exit 0; sleep 1; done
docker logs --tail 20 airline-web; exit 1
