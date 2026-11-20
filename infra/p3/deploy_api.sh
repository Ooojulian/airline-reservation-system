#!/usr/bin/env bash
# Sube backend/ (último commit) a S3 y lo despliega en la EC2: reconstruye la imagen y relanza la API.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
cd "$(dirname "$0")/../.."
B=airline-system-$(aws sts get-caller-identity --query Account --output text)
git archive --format=tar.gz -o /tmp/backend.tgz HEAD backend
aws s3 cp /tmp/backend.tgz s3://$B/code/backend.tgz --quiet
bash infra/ssm_run.sh "set -e; cd /opt/airline-system && aws s3 cp s3://$B/code/backend.tgz /tmp/backend.tgz --quiet && tar xzf /tmp/backend.tgz && bash backend/run_api.sh --build 2>&1 | tail -3"
