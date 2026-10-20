#!/usr/bin/env bash
# Glue: conexiones JDBC, Data Catalog, crawlers y job ETL (idempotente). Credenciales desde SSM en tiempo de ejecución.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
HERE="$(cd "$(dirname "$0")" && pwd)"
BUCKET=airline-system-930581488240
ROLE=arn:aws:iam::930581488240:role/LabRole
SG=sg-02ee4ddb48ac84e4d
JOB=airline-etl-oltp-to-olap
TAGS="Project=airline-system"

aws s3 cp "$HERE/glue/etl_oltp_to_olap.py" s3://$BUCKET/glue/etl_oltp_to_olap.py

# Subnet/AZ reales de la instancia RDS
AZ=$(aws rds describe-db-instances --db-instance-identifier airline-pg --query 'DBInstances[0].AvailabilityZone' --output text)
SUBNET=$(aws rds describe-db-instances --db-instance-identifier airline-pg --query "DBInstances[0].DBSubnetGroup.Subnets[?SubnetAvailabilityZone.Name=='$AZ'].SubnetIdentifier|[0]" --output text)
HOST=$(aws rds describe-db-instances --db-instance-identifier airline-pg --query 'DBInstances[0].Endpoint.Address' --output text)
DBU=$(aws ssm get-parameter --name /airline/db/user --query Parameter.Value --output text)
DBP=$(aws ssm get-parameter --name /airline/db/password --with-decryption --query Parameter.Value --output text)
echo "AZ=$AZ SUBNET=$SUBNET HOST=$HOST"

mkconn() { # nombre base
  local f; f=$(mktemp); chmod 600 $f
  python3 - "$1" "$2" "$HOST" "$DBU" "$DBP" "$SUBNET" "$AZ" "$SG" > $f <<'PY'
import json,sys
n,db,host,u,p,sub,az,sg=sys.argv[1:]
print(json.dumps({"Name":n,"ConnectionType":"JDBC","ConnectionProperties":{
 "JDBC_CONNECTION_URL":f"jdbc:postgresql://{host}:5432/{db}","USERNAME":u,"PASSWORD":p,"JDBC_ENFORCE_SSL":"false"},
 "PhysicalConnectionRequirements":{"SubnetId":sub,"AvailabilityZone":az,"SecurityGroupIdList":[sg]}}))
PY
  if aws glue get-connection --name $1 >/dev/null 2>&1; then
    aws glue update-connection --name $1 --connection-input file://$f
  else
    aws glue create-connection --connection-input file://$f --tags $TAGS
  fi
  rm -f $f
}
mkconn airline-oltp-conn airline_oltp
mkconn airline-olap-conn airline_olap

for c in airline-oltp-conn airline-olap-conn; do
  aws glue start-connection-test --connection-name $c 2>/dev/null || echo "(start-connection-test no disponible en la CLI; se omite)"
done

for d in airline_oltp airline_olap; do
  aws glue get-database --name $d >/dev/null 2>&1 || aws glue create-database --database-input "{\"Name\":\"$d\"}"
done

mkcrawler() { # nombre db conexion path
  local tg="{\"JdbcTargets\":[{\"ConnectionName\":\"$3\",\"Path\":\"$4\"}]}"
  if aws glue get-crawler --name $1 >/dev/null 2>&1; then
    aws glue update-crawler --name $1 --role $ROLE --database-name $2 --targets "$tg"
  else
    aws glue create-crawler --name $1 --role $ROLE --database-name $2 --targets "$tg" --tags $TAGS
  fi
}
mkcrawler airline-oltp-crawler airline_oltp airline-oltp-conn 'airline_oltp/public/%'
mkcrawler airline-olap-crawler airline_olap airline-olap-conn 'airline_olap/public/%'

runcrawler() {
  aws glue start-crawler --name $1 || true
  while :; do
    S=$(aws glue get-crawler --name $1 --query Crawler.State --output text)
    [ "$S" = READY ] && break; sleep 15
  done
  aws glue get-crawler --name $1 --query 'Crawler.LastCrawl.[Status,ErrorMessage]' --output text
}
runcrawler airline-oltp-crawler
runcrawler airline-olap-crawler
for d in airline_oltp airline_olap; do
  echo "$d: $(aws glue get-tables --database-name $d --query 'length(TableList)')"
done

# Job
JOBDEF=$(mktemp)
cat > $JOBDEF <<JSON
{"Role":"$ROLE","GlueVersion":"4.0","WorkerType":"G.1X","NumberOfWorkers":2,"Timeout":15,"MaxRetries":0,
 "Command":{"Name":"glueetl","ScriptLocation":"s3://$BUCKET/glue/etl_oltp_to_olap.py","PythonVersion":"3"},
 "Connections":{"Connections":["airline-oltp-conn","airline-olap-conn"]},
 "DefaultArguments":{"--job-language":"python","--enable-continuous-cloudwatch-log":"true","--TempDir":"s3://$BUCKET/glue/tmp/"}}
JSON
if aws glue get-job --job-name $JOB >/dev/null 2>&1; then
  aws glue update-job --job-name $JOB --job-update "file://$JOBDEF" >/dev/null
else
  aws glue create-job --name $JOB --cli-input-json "file://$JOBDEF" --tags $TAGS
fi
rm -f $JOBDEF

RUN=$(aws glue start-job-run --job-name $JOB --query JobRunId --output text)
echo "JobRunId=$RUN"
while :; do
  S=$(aws glue get-job-run --job-name $JOB --run-id $RUN --query JobRun.JobRunState --output text)
  case $S in SUCCEEDED|FAILED|ERROR|STOPPED|TIMEOUT) break;; esac; sleep 20
done
aws glue get-job-run --job-name $JOB --run-id $RUN --query 'JobRun.[JobRunState,StartedOn,CompletedOn,ExecutionTime,DPUSeconds,ErrorMessage]' --output text
[ "$S" = SUCCEEDED ]
