#!/usr/bin/env bash
# Uso: emr/run_emr.sh sample|full
#  sample -> raw-sample/ -> curated-sample/ ; full -> raw/ -> curated/
# Crea un clúster EMR efímero (auto-terminate), corre curate.py y espera a que termine.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
B=s3://airline-system-930581488240
MODE="${1:-sample}"
case "$MODE" in
  sample) IN=$B/raw-sample/; OUT=$B/curated-sample/ ;;
  full)   IN=$B/raw/;        OUT=$B/curated/ ;;
  *) echo "uso: $0 sample|full"; exit 1 ;;
esac
DIR="$(cd "$(dirname "$0")" && pwd)"
aws s3 cp "$DIR/curate.py" $B/emr/curate.py --quiet
REL=$(aws emr list-release-labels --query 'ReleaseLabels[?starts_with(@,`emr-7.`)]' --output text | tr '\t' '\n' | sort -V | tail -1)
echo "release: $REL"
CID=$(aws emr create-cluster --name "airline-curate-$MODE" --release-label "$REL" \
  --applications Name=Spark \
  --instance-groups InstanceGroupType=MASTER,InstanceCount=1,InstanceType=m4.large \
                    InstanceGroupType=CORE,InstanceCount=2,InstanceType=m4.large \
  --service-role EMR_DefaultRole \
  --ec2-attributes InstanceProfile=EMR_EC2_DefaultRole,SubnetId=subnet-04cc25c5f1dc63df5 \
  --log-uri $B/emr/logs/ --auto-terminate \
  --steps "Type=Spark,Name=curate-$MODE,ActionOnFailure=TERMINATE_CLUSTER,Args=[--deploy-mode,cluster,$B/emr/curate.py,--input,$IN,--output,$OUT]" \
  --query ClusterId --output text)
echo "cluster: $CID"
aws emr wait cluster-terminated --cluster-id "$CID" || true
aws emr describe-cluster --cluster-id "$CID" --query 'Cluster.[Status.State,Status.StateChangeReason.Message,NormalizedInstanceHours]' --output text
aws emr list-steps --cluster-id "$CID" --query 'Steps[].[Name,Status.State]' --output text
echo "activos:"; aws emr list-clusters --active --query 'Clusters[].Id' --output text
