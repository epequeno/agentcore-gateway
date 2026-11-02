"""Phase 4 evidence: one call through every Gateway target type.

The Gateway catalog is read with the caller's Cognito token, then each target
is exercised through ``tools/call``. Tool names carry the target prefix, so an
MCP call that bypassed the Gateway could not use these names. Gateway metrics
are queried afterwards as the service-side attribution record.
"""

import sys
import uuid
from datetime import UTC, datetime

import boto3

from agent.config import settings
from evals.identity import access_token, mcp_url

# (target, tool, arguments, expected substring)
CHECKS = [
    ("echo-target", "echo-target___echo", {"text": "echo-target-ok"}, "echo-target-ok"),
    ("crm-mcp", "crm-mcp___getAccount", {}, "ACME-42"),
    ("openapi-status", "openapi-status___fetchStatus", {}, "openapi-target-ok"),
    (
        "workflow",
        "workflow___StartExecution",
        {"input": '{"who":"agent"}'},
        "executionArn",
    ),
    (
        "toolbox",
        "toolbox___execute_python",
        {"code": 'print("toolbox-ok")'},
        "toolbox-ok",
    ),
    (
        "toolbox",
        "toolbox___browse_page",
        {"url": "https://example.com"},
        "Example Domain",
    ),
]


def _text(result) -> str:
    """Flatten an MCP tool result to text for a substring check."""
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            parts.append(str(text))
    return "\n".join(parts) or str(result)


def _metric_snapshot(gateway_id: str, expected_names: set[str]) -> list[str]:
    """Return recent Gateway target and tool metrics, if the service published them."""
    cloudwatch = boto3.client("cloudwatch", region_name=settings.region)
    metrics = cloudwatch.list_metrics(Namespace="AWS/Bedrock-AgentCore")["Metrics"]
    interesting = []
    for metric in metrics:
        name = metric.get("MetricName", "")
        if name not in {"TargetType", "Invocations"}:
            continue
        dimensions = {d["Name"]: d["Value"] for d in metric.get("Dimensions", [])}
        tool_name = dimensions.get("Name", "")
        if (
            name == "Invocations"
            and "TargetType" not in dimensions
            and gateway_id not in " ".join(dimensions.values())
            and tool_name not in expected_names
        ):
            continue
        interesting.append(
            f"{name} " + ", ".join(f"{key}={value}" for key, value in dimensions.items())
        )
    return sorted(set(interesting))


def main() -> int:
    missing = [
        name
        for name, value in (
            ("AGENTCORE_GATEWAY_URL", settings.gateway_url),
            ("COGNITO_USER_POOL_ID", settings.user_pool_id),
            ("COGNITO_CLIENT_ID", settings.cognito_client_id),
            ("IDENTITY_TEST_PASSWORD", settings.identity_password),
        )
        if not value
    ]
    if missing:
        print(f"set {', '.join(missing)}", file=sys.stderr)
        return 2

    from strands.tools.mcp import MCPClient

    token = access_token()
    session_id = f"tools-{uuid.uuid4().hex[:8]}"
    started = datetime.now(UTC)
    client = MCPClient(
        url=mcp_url(),
        headers={
            "Authorization": f"Bearer {token}",
            "x-amzn-bedrock-agentcore-policy-session-id": session_id,
        },
    )
    failures = []
    with client:
        names = [tool.tool_name for tool in client.list_tools_sync()]
        print(f"catalog: {len(names)} tools")
        for name in names:
            print(f"  {name}")

        for target, tool, arguments, expected in CHECKS:
            if tool not in names:
                print(f"FAIL {target}: {tool} is not in the catalog")
                failures.append(target)
                continue
            try:
                result = client.call_tool_sync(f"{target}-{uuid.uuid4().hex[:4]}", tool, arguments)
                text = _text(result)
                is_error = bool(getattr(result, "isError", False))
            except Exception as exc:  # noqa: BLE001 - the failure is the evidence
                print(f"FAIL {target}: {type(exc).__name__}: {exc}")
                failures.append(target)
                continue
            if is_error or expected.lower() not in text.lower():
                print(f"FAIL {target}: expected {expected!r}, got {text[:500]!r}")
                failures.append(target)
            else:
                print(f"PASS {target}: {text[:200].replace(chr(10), ' ')}")

    # Metrics are batched at one-minute intervals; this is a best-effort
    # attribution snapshot, not a gate on the call itself.
    metrics = _metric_snapshot(settings.gateway_id, {tool for _, tool, _, _ in CHECKS})
    print("gateway metrics:")
    for line in metrics:
        print(f"  {line}")
    if not metrics:
        print("  none found in the current window (target calls still returned via Gateway)")

    if failures:
        print(f"FAILED targets: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"tool-surface checks passed at {started.isoformat()} ({len(CHECKS)} calls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
