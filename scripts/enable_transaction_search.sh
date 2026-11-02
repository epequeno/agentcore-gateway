#!/usr/bin/env bash
# One-time, account-level prerequisite for AgentCore Observability and
# Evaluations: X-Ray must deliver spans to CloudWatch Logs.
#
# The account id is read at runtime, never stored in the repo.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
POLICY="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TransactionSearchXRayAccess",
      "Effect": "Allow",
      "Principal": {"Service": "xray.amazonaws.com"},
      "Action": "logs:PutLogEvents",
      "Resource": [
        "arn:aws:logs:${REGION}:${ACCOUNT}:log-group:aws/spans:*",
        "arn:aws:logs:${REGION}:${ACCOUNT}:log-group:/aws/application-signals/data:*"
      ],
      "Condition": {
        "ArnLike": {"aws:SourceArn": "arn:aws:xray:${REGION}:${ACCOUNT}:*"},
        "StringEquals": {"aws:SourceAccount": "${ACCOUNT}"}
      }
    }
  ]
}
JSON
)"

aws logs put-resource-policy \
  --policy-name AgentCoreTransactionSearch \
  --policy-document "$POLICY" \
  --region "$REGION"

# Idempotent: the API rejects a no-op change with "already set to CloudWatchLogs".
aws xray update-trace-segment-destination \
  --destination CloudWatchLogs \
  --region "$REGION" 2>/dev/null || true

# Index every trace during a measurement run. Reset to 1 afterwards to stay in
# the free tier: aws xray update-indexing-rule --name Default \
#   --rule '{"Probabilistic": {"DesiredSamplingPercentage": 1}}'
aws xray update-indexing-rule \
  --name Default \
  --rule '{"Probabilistic": {"DesiredSamplingPercentage": 100}}' \
  --region "$REGION"

echo "Transaction Search enabled in ${REGION}; spans may take ~10 minutes to become searchable."
