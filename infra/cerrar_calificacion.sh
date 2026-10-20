#!/usr/bin/env bash
# Revierte abrir_calificacion.sh: 8000 y 3000 vuelven a quedar solo para las IPs del equipo.
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
SG=sg-0b22c99798f6dc60a
for p in 8000 3000; do
  aws ec2 revoke-security-group-ingress --group-id $SG --protocol tcp --port $p --cidr 0.0.0.0/0 >/dev/null 2>&1 \
    && echo "puerto $p cerrado a internet" || echo "puerto $p no estaba abierto"
done
