#!/usr/bin/env bash
# Detiene RDS airline-pg y la EC2 airline-api (RDS se reinicia solo tras 7 dias detenido).
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
aws rds stop-db-instance --db-instance-identifier airline-pg >/dev/null && echo "RDS airline-pg: deteniendo"
IDS=$(aws ec2 describe-instances --filters Name=tag:Name,Values=airline-api Name=instance-state-name,Values=running,pending --query 'Reservations[].Instances[].InstanceId' --output text)
[ -n "$IDS" ] && aws ec2 stop-instances --instance-ids $IDS >/dev/null && echo "EC2 $IDS: deteniendo" || echo "EC2 airline-api no encontrada/en ejecucion"
