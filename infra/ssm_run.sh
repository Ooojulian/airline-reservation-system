#!/usr/bin/env bash
# Uso: ssm_run.sh "<comando>"  -> ejecuta en la EC2 airline-api y muestra la salida.
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=airline-api Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].InstanceId' --output text)
CID=$(aws ssm send-command --instance-ids $ID --document-name AWS-RunShellScript --timeout-seconds 3000 \
  --parameters "$(python3 -c 'import json,sys;print(json.dumps({"commands":[sys.argv[1]],"executionTimeout":["3000"]}))' "$1")" \
  --query Command.CommandId --output text)
while :; do
  S=$(aws ssm get-command-invocation --command-id $CID --instance-id $ID --query Status --output text 2>/dev/null)
  case "$S" in Success|Failed|Cancelled|TimedOut) break;; esac; sleep 5
done
aws ssm get-command-invocation --command-id $CID --instance-id $ID --query '[StandardOutputContent,StandardErrorContent]' --output text
echo "== status: $S"; [ "$S" = Success ]
