#!/usr/bin/env bash
# Borra Firehose y el stream (lo único del Parcial 3 que cobra por hora). Los datos en S3 se conservan.
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
aws firehose delete-delivery-stream --delivery-stream-name airline-events-to-s3 2>/dev/null && echo "Firehose: borrando"
aws kinesis delete-stream --stream-name airline-events --enforce-consumer-deletion 2>/dev/null && echo "stream: borrando"
aws kinesis list-streams --query StreamNames --output text
