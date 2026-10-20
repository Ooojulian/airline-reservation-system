#!/usr/bin/env bash
# Infra base idempotente: SGs, subnet group, SSM, RDS PostgreSQL 16 (airline-pg), S3, VPC endpoint S3.
set -uo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
VPC=vpc-055e559fe133bbe1e
MYIP=181.32.203.201/32
ACCT=930581488240
BUCKET=airline-system-$ACCT
TAG="Key=Project,Value=airline-system"
SUBNETS="subnet-04cc25c5f1dc63df5 subnet-019f193722650f8a8 subnet-09e5318c07ecfddce"

mksg() {
  local id
  id=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$1 Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text)
  if [ "$id" = "None" ]; then
    id=$(aws ec2 create-security-group --group-name $1 --description "$2" --vpc-id $VPC \
      --tag-specifications "ResourceType=security-group,Tags=[{$TAG}]" --query GroupId --output text)
  fi
  echo $id
}
rule() { # sg proto port cidr|sg source
  local out
  if [ "$4" = cidr ]; then
    out=$(aws ec2 authorize-security-group-ingress --group-id $1 --protocol $2 --port $3 --cidr $5 2>&1)
  else
    out=$(aws ec2 authorize-security-group-ingress --group-id $1 --protocol $2 --port $3 --source-group $5 2>&1)
  fi
  [[ $? -eq 0 || "$out" == *Duplicate* ]] || { echo "ERROR regla: $out"; exit 1; }
}

API_SG=$(mksg airline-api-sg "Airline API")
DB_SG=$(mksg airline-db-sg "Airline DB")
rule $API_SG tcp 8000 cidr $MYIP
rule $API_SG tcp 22 cidr $MYIP
rule $DB_SG tcp 5432 cidr $MYIP
rule $DB_SG tcp 5432 sg $API_SG
rule $DB_SG tcp 0-65535 sg $DB_SG   # autorreferencia para Glue
echo "API_SG=$API_SG DB_SG=$DB_SG"

aws rds describe-db-subnet-groups --db-subnet-group-name airline-db-subnets >/dev/null 2>&1 || \
  aws rds create-db-subnet-group --db-subnet-group-name airline-db-subnets \
    --db-subnet-group-description "Airline DB subnets" --subnet-ids $SUBNETS --tags $TAG >/dev/null

if ! aws ssm get-parameter --name /airline/db/password >/dev/null 2>&1; then
  PW=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 24)
  aws ssm put-parameter --name /airline/db/password --type SecureString --value "$PW" --tags $TAG >/dev/null
  unset PW
fi
aws ssm get-parameter --name /airline/db/user >/dev/null 2>&1 || \
  aws ssm put-parameter --name /airline/db/user --type String --value airline_admin --tags $TAG >/dev/null
PW=$(aws ssm get-parameter --name /airline/db/password --with-decryption --query Parameter.Value --output text)

# RDS PostgreSQL 16, db.t3.micro, single-AZ, gp3 20GB, cifrado (Aurora no permitido por el harness)
if ! aws rds describe-db-instances --db-instance-identifier airline-pg >/dev/null 2>&1; then
  PGV=$(aws rds describe-db-engine-versions --engine postgres --query 'DBEngineVersions[?starts_with(EngineVersion,`16.`)].EngineVersion' --output text | tr '\t' '\n' | sort -V | tail -1)
  echo "Creando RDS PostgreSQL $PGV"
  aws rds create-db-instance --db-instance-identifier airline-pg --engine postgres --engine-version $PGV \
    --db-instance-class db.t3.micro --allocated-storage 20 --storage-type gp3 --storage-encrypted \
    --master-username airline_admin --master-user-password "$PW" --backup-retention-period 1 \
    --no-multi-az --no-deletion-protection --publicly-accessible \
    --db-subnet-group-name airline-db-subnets --vpc-security-group-ids $DB_SG --db-name postgres --tags $TAG >/dev/null || exit 1
fi
unset PW
aws rds wait db-instance-available --db-instance-identifier airline-pg
HOST=$(aws rds describe-db-instances --db-instance-identifier airline-pg --query 'DBInstances[0].Endpoint.Address' --output text)
aws ssm put-parameter --name /airline/db/host --type String --value "$HOST" --overwrite >/dev/null
echo "DB_HOST=$HOST"

if ! aws s3api head-bucket --bucket $BUCKET 2>/dev/null; then
  aws s3api create-bucket --bucket $BUCKET >/dev/null
fi
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket $BUCKET --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-tagging --bucket $BUCKET --tagging 'TagSet=[{Key=Project,Value=airline-system}]'

VPCE=$(aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values=$VPC Name=service-name,Values=com.amazonaws.us-east-1.s3 Name=vpc-endpoint-type,Values=Gateway --query 'VpcEndpoints[0].VpcEndpointId' --output text)
if [ "$VPCE" = "None" ]; then
  RTS=$(aws ec2 describe-route-tables --filters Name=vpc-id,Values=$VPC --query 'RouteTables[].RouteTableId' --output text)
  VPCE=$(aws ec2 create-vpc-endpoint --vpc-id $VPC --service-name com.amazonaws.us-east-1.s3 --vpc-endpoint-type Gateway \
    --route-table-ids $RTS --tag-specifications "ResourceType=vpc-endpoint,Tags=[{$TAG}]" --query VpcEndpoint.VpcEndpointId --output text)
fi
echo "VPCE=$VPCE BUCKET=$BUCKET"
