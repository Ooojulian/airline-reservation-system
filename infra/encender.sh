#!/usr/bin/env bash
# Inicia RDS airline-pg y la EC2 airline-api. Ojo: la IP publica de la EC2 cambia si no tiene Elastic IP.
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
aws rds start-db-instance --db-instance-identifier airline-pg >/dev/null && echo "RDS airline-pg: iniciando"
IDS=$(aws ec2 describe-instances --filters Name=tag:Name,Values=airline-api Name=instance-state-name,Values=stopped,stopping --query 'Reservations[].Instances[].InstanceId' --output text)
[ -n "$IDS" ] && aws ec2 start-instances --instance-ids $IDS >/dev/null && echo "EC2 $IDS: iniciando" || echo "EC2 airline-api no encontrada/detenida"
aws rds wait db-instance-available --db-instance-identifier airline-pg && echo "RDS disponible"

# Relanzar la API en la EC2 (run_api.sh lee credenciales de SSM) e imprimir la nueva IP publica
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=airline-api --query 'Reservations[0].Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids $ID
for i in $(seq 1 60); do
  [ "$(aws ssm describe-instance-information --filters Key=InstanceIds,Values=$ID --query 'InstanceInformationList[0].PingStatus' --output text)" = Online ] && break; sleep 10
done
CID=$(aws ssm send-command --instance-ids $ID --document-name AWS-RunShellScript \
  --parameters 'commands=["bash /opt/airline-system/backend/run_api.sh","bash /opt/airline-system/frontend/run_web.sh"]' --query Command.CommandId --output text)
aws ssm wait command-executed --command-id $CID --instance-id $ID 2>/dev/null
aws ssm get-command-invocation --command-id $CID --instance-id $ID --query 'StandardOutputContent' --output text | tail -5
IP=$(aws ec2 describe-instances --instance-ids $ID --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "API: http://$IP:8000  (health: http://$IP:8000/health)"
echo "Web: http://$IP:3000  (panel: http://$IP:3000/manager)"
