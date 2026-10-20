#!/usr/bin/env bash
# Abre la API (8000) y el frontend (3000) a internet SOLO durante la ventana de calificacion.
# La base de datos (5432) nunca se abre. Cerrar al terminar con cerrar_calificacion.sh.
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
SG=sg-0b22c99798f6dc60a
for p in 8000 3000; do
  aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port $p --cidr 0.0.0.0/0 >/dev/null 2>&1 \
    && echo "puerto $p abierto a 0.0.0.0/0" || echo "puerto $p ya estaba abierto"
done
echo "URLs: http://18.208.1.79:8000  http://18.208.1.79:3000"
