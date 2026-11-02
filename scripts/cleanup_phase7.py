"""Delete CloudWatch log groups that survive CloudFormation stack deletion.

CloudWatch does not delete a Lambda's log group when the function is deleted.
The same is true for AgentCore runtime/evaluation log groups created by the
service. `just down` runs this after `cdk destroy` so a teardown does not leave
storage billing behind. Prefixes are deliberately scoped to this stack's names;
the shared CDK bootstrap log groups are not touched.
"""

import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import settings  # noqa: E402

LOG_PREFIXES = (
    "/aws/lambda/AgentCoreGatewayStack-",
    "/aws/bedrock-agentcore/runtimes/agentcore_",
    "/aws/bedrock-agentcore/runtimes/harness_agentcore_",
    "/aws/bedrock-agentcore/evaluations/results/agentcore_",
)


def main() -> int:
    logs = boto3.client("logs", region_name=settings.region)
    deleted = 0

    for page in logs.get_paginator("describe_log_groups").paginate():
        for group in page.get("logGroups", []):
            name = group["logGroupName"]
            if not name.startswith(LOG_PREFIXES):
                continue
            try:
                logs.delete_log_group(logGroupName=name)
                print(f"deleted log group {name}")
                deleted += 1
            except Exception as exc:  # noqa: BLE001 - report and keep going
                print(f"{name}: delete failed: {exc}", file=sys.stderr)

    print(f"deleted {deleted} log group(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
