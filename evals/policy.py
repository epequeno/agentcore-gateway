"""Phase 3 evidence: Cedar/Dogwood decisions and their CloudWatch record.

This is deliberately separate from ``evals/run.py``: the policy fixture makes
tool calls that a normal eval session never makes (a quota over the limit, and
two CRM calls in one policy session), and it invokes the Runtime once to check
that the model reports a refusal rather than a tool error.

Exports: AGENTCORE_GATEWAY_URL, AGENTCORE_RUNTIME_ARN, POLICY_ENGINE_ARN,
AGENTCORE_GATEWAY_ID, COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID,
IDENTITY_TEST_PASSWORD.
"""

import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

import boto3

from agent.config import settings
from evals.identity import access_token, mcp_url

PROTOCOL = "2025-11-25"


def _mcp(name: str, arguments: dict, session: str, token: str) -> dict:
    """Call one Gateway tool and return the parsed JSON-RPC response."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    ).encode()
    request = urllib.request.Request(
        mcp_url(),
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            "x-amzn-bedrock-agentcore-policy-session-id": session,
        },
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode())


def _denied(response: dict) -> bool:
    message = response.get("error", {}).get("message", "")
    return "policy enforcement" in message


def _agent_refusal(token: str) -> str:
    """Invoke the Runtime once with an over-limit request."""
    if not settings.runtime_arn:
        raise RuntimeError("AGENTCORE_RUNTIME_ARN is required for the agent check")
    session = f"policy-refusal-{uuid.uuid4().hex}"
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=settings.runtime_arn,
        runtimeSessionId=session,
        payload=json.dumps(
            {
                "prompt": (
                    "Use the policy-quota spend tool to spend 150 credits. "
                    "Report what the tool says. If policy blocks it, say policy refused the action."
                ),
                "authToken": token,
                "actorId": "policy-eval",
                "sessionId": session,
            }
        ),
    )
    raw = response["response"]
    data = raw.read() if hasattr(raw, "read") else b"".join(raw)
    result = json.loads(data.decode())
    if "error" in result:
        raise RuntimeError(f"Runtime returned an error: {result['error']}")
    return result["response"]


def _metric_sum(metric: str, policy_id: str) -> float:
    """Read one policy decision metric, retrying for CloudWatch's lag."""
    if not (settings.policy_engine_arn and settings.gateway_id):
        raise RuntimeError("POLICY_ENGINE_ARN and AGENTCORE_GATEWAY_ID are required")
    engine_id = settings.policy_engine_arn.rsplit("/", 1)[-1]
    client = boto3.client("cloudwatch", region_name=settings.region)
    dimensions = [
        {"Name": "Policy", "Value": policy_id},
        {"Name": "TargetResource", "Value": settings.gateway_id},
        {"Name": "OperationName", "Value": "AuthorizeAction"},
        {"Name": "PolicyEngine", "Value": engine_id},
        {"Name": "Mode", "Value": "ENFORCE"},
    ]
    for _ in range(8):
        end = datetime.now(UTC)
        response = client.get_metric_statistics(
            Namespace="AWS/Bedrock-AgentCore",
            MetricName=metric,
            Dimensions=dimensions,
            StartTime=end - timedelta(minutes=30),
            EndTime=end,
            Period=300,
            Statistics=["Sum"],
        )
        total = sum(point["Sum"] for point in response["Datapoints"])
        if total:
            return total
        time.sleep(15)
    return 0.0


def main() -> int:
    token = access_token()
    session = f"policy-check-{uuid.uuid4().hex[:8]}"

    allowed = _mcp("policy-quota___spend", {"amount": 100}, session, token)
    denied = _mcp("policy-quota___spend", {"amount": 101}, session, token)
    assert not _denied(allowed), allowed
    assert _denied(denied), denied
    print("parameter rule: 100 allowed, 101 denied")

    temporal = f"temporal-check-{uuid.uuid4().hex[:8]}"
    first = _mcp("crm-mcp___getAccount", {}, temporal, token)
    second = _mcp("crm-mcp___getAccount", {}, temporal, token)
    assert not _denied(first), first
    assert _denied(second), second
    print("temporal rule: first CRM call allowed, second denied")

    response = _agent_refusal(token)
    lowered = response.lower()
    assert "policy" in lowered and (
        "block" in lowered or "refus" in lowered or "denied" in lowered
    ), response
    print(f"agent refusal: {response.strip()}")

    controls = boto3.client("bedrock-agentcore-control", region_name=settings.region)
    engine_id = settings.policy_engine_arn.rsplit("/", 1)[-1]
    policies = {
        policy["name"]: policy["policyId"]
        for policy in controls.list_policies(policyEngineId=engine_id)["policies"]
    }
    for metric, name in (
        ("DenyDecisions", "quota_limit"),
        ("DenyDecisions", "crm_once_per_session"),
        ("AllowDecisions", "gateway_baseline_permit"),
    ):
        total = _metric_sum(metric, policies[name])
        assert total > 0, f"{metric} for {name} is zero"
        print(f"CloudWatch {metric} {name}: {total:.0f}")

    print("policy checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
