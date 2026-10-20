#!/usr/bin/env bash
# Crea (idempotente) la EC2 airline-api: t3.small, AL2023, gp3 8GB, LabInstanceProfile.
set -uo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=airline-api-sg --query 'SecurityGroups[0].GroupId' --output text)
SUBNET=subnet-04cc25c5f1dc63df5
ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=airline-api Name=instance-state-name,Values=pending,running,stopped,stopping --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$ID" = "None" ]; then
  AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query Parameter.Value --output text)
  UD=$(mktemp)
  cat > $UD <<'UDEOF'
#!/bin/bash
dnf install -y docker postgresql16 python3-pip git unzip
systemctl enable --now docker
usermod -aG docker ec2-user
UDEOF
  ID=$(aws ec2 run-instances --image-id $AMI --instance-type t3.small --subnet-id $SUBNET --security-group-ids $SG \
    --iam-instance-profile Name=LabInstanceProfile --associate-public-ip-address \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=8,VolumeType=gp3,DeleteOnTermination=true}' \
    --user-data file://$UD \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=airline-api},{Key=Project,Value=airline-system}]' 'ResourceType=volume,Tags=[{Key=Project,Value=airline-system}]' \
    --query 'Instances[0].InstanceId' --output text)
  rm -f $UD
fi
echo "INSTANCE_ID=$ID"
aws ec2 wait instance-running --instance-ids $ID
for i in $(seq 1 60); do
  P=$(aws ssm describe-instance-information --filters Key=InstanceIds,Values=$ID --query 'InstanceInformationList[0].PingStatus' --output text)
  [ "$P" = "Online" ] && break; sleep 10
done
echo "SSM=$P"
echo "PUBLIC_IP=$(aws ec2 describe-instances --instance-ids $ID --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
