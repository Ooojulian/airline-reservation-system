#!/usr/bin/env bash
# Crea el stream (1 shard provisionado, retención 24 h) y la entrega Firehose -> S3 raw/events/.
# El shard cobra por hora: borrarlo con stream_down.sh al terminar de trabajar.
set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
ACC=$(aws sts get-caller-identity --query Account --output text)
B=airline-system-$ACC; S=airline-events; F=airline-events-to-s3
ROLE=arn:aws:iam::$ACC:role/LabRole

aws kinesis create-stream --stream-name $S --shard-count 1 --stream-mode-details StreamMode=PROVISIONED 2>/dev/null \
  && echo "creando stream $S" || echo "stream $S ya existe"
aws kinesis wait stream-exists --stream-name $S
SARN=$(aws kinesis describe-stream-summary --stream-name $S --query StreamDescriptionSummary.StreamARN --output text)

if ! aws firehose describe-delivery-stream --delivery-stream-name $F >/dev/null 2>&1; then
  aws firehose create-delivery-stream --delivery-stream-name $F --delivery-stream-type KinesisStreamAsSource \
    --kinesis-stream-source-configuration "KinesisStreamARN=$SARN,RoleARN=$ROLE" \
    --extended-s3-destination-configuration "$(cat <<JSON
{"RoleARN":"$ROLE","BucketARN":"arn:aws:s3:::$B","Prefix":"raw/events/","ErrorOutputPrefix":"raw/errors/",
 "BufferingHints":{"IntervalInSeconds":60,"SizeInMBs":5},"CompressionFormat":"GZIP",
 "ProcessingConfiguration":{"Enabled":true,"Processors":[{"Type":"AppendDelimiterToRecord","Parameters":[]}]}}
JSON
)" >/dev/null
  echo "creando Firehose $F"
fi
for i in $(seq 1 40); do
  st=$(aws firehose describe-delivery-stream --delivery-stream-name $F --query DeliveryStreamDescription.DeliveryStreamStatus --output text)
  [ "$st" = ACTIVE ] && break; sleep 10
done
echo "Firehose: $st"
aws kinesis describe-stream-summary --stream-name $S \
  --query 'StreamDescriptionSummary.[StreamStatus,OpenShardCount,RetentionPeriodHours,StreamModeDetails.StreamMode]' --output text
