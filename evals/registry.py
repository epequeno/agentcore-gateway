"""Phase 5 evidence: a fresh consumer finds the agent and tools in Registry.

The check uses only the data-plane discovery API. It does not know the record
IDs or exact names: it asks a natural-language question, then reads the ranked
results. Registry search is eventually consistent after approval, so the first
few calls retry while the index catches up.

The agent record's custom descriptor carries the deployed Runtime ARN. Comparing
that value to ``AGENTCORE_RUNTIME_ARN`` is the "catalog matches the deployment"
half of the acceptance check.
"""

import json
import sys
import time
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

from agent.config import settings

AGENT_QUERY = (
    "support operations agent that answers customer questions about accounts "
    "and billing using CRM and workflow tools"
)
TOOLS_QUERY = (
    "tools for customer account lookup, spending limits, customer support "
    "workflows, code execution and web browsing"
)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def search(
    client,
    registry_arn: str,
    query: str,
    record_type: str,
    timeout: float = 300.0,
) -> dict:
    """Return the first approved record of ``record_type`` for a semantic query."""
    deadline = time.monotonic() + timeout
    delay = 2.0
    last = []
    while time.monotonic() < deadline:
        response = client.search_discoverable_registry_records(
            registryIds=[registry_arn],
            searchQuery=query,
            maxResults=20,
        )
        last = response.get("registryRecords", [])
        for record in last:
            if record.get("recordType") == record_type:
                return record
        _sleep(delay)
        delay = min(delay * 1.5, 15.0)
    names = ", ".join(f"{r.get('name')} ({r.get('recordType')})" for r in last)
    raise RuntimeError(f"no {record_type} record matched {query!r}; last results: {names}")


def _detail(client, registry_arn: str, record: dict) -> dict:
    """Fetch a record's descriptor from the discovery plane.

    Search already returns descriptors in the current API. The batch call is a
    fallback for response-shape changes, not a control-plane read.
    """
    if record.get("descriptors"):
        return record
    response = client.batch_get_discoverable_registry_record(
        entries=[{"registryId": registry_arn, "recordIds": [record["recordId"]]}]
    )
    records = response.get("registryRecords", [])
    if not records:
        raise RuntimeError(f"no descriptor returned for {record.get('name')}")
    return records[0]


def _custom_payload(record: dict) -> dict:
    descriptor = (record.get("descriptors") or {}).get("custom") or {}
    data = descriptor.get("data")
    if not data:
        raise RuntimeError(f"{record.get('name')} has no custom descriptor data")
    return json.loads(data)


def _tool_names(record: dict) -> list[str]:
    descriptor = (record.get("descriptors") or {}).get("mcpServer") or {}
    tools = (descriptor.get("additionalData") or {}).get("tools") or {}
    data = tools.get("data")
    if not data:
        raise RuntimeError(f"{record.get('name')} has no MCP tools descriptor")
    return [tool["name"] for tool in json.loads(data).get("tools", [])]


def main() -> int:
    missing = [
        name
        for name, value in (
            ("AGENTCORE_REGISTRY_ARN", settings.registry_arn),
            ("AGENTCORE_RUNTIME_ARN", settings.runtime_arn),
        )
        if not value
    ]
    if missing:
        print(f"set {', '.join(missing)}", file=sys.stderr)
        return 2

    started = datetime.now(UTC)
    # A new client is the whole consumer path: no control-plane handles, no
    # record ids, and no knowledge of the record names.
    client = boto3.client("agent-registry", region_name=settings.region)
    try:
        agent = _detail(
            client,
            settings.registry_arn,
            search(client, settings.registry_arn, AGENT_QUERY, "AGENT"),
        )
        tools = _detail(
            client,
            settings.registry_arn,
            search(client, settings.registry_arn, TOOLS_QUERY, "MCP"),
        )
    except (ClientError, RuntimeError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    agent_payload = _custom_payload(agent)
    runtime_arn = agent_payload.get("runtimeArn")
    names = agent_payload.get("tools", [])

    print(f"agent: {agent['name']} ({agent.get('recordType')})")
    print(f"  description: {agent.get('description', '')}")
    print(f"  runtime ARN: {runtime_arn}")
    print(f"tools: {tools['name']} ({tools.get('recordType')})")
    print(f"  catalog: {', '.join(_tool_names(tools))}")

    if runtime_arn != settings.runtime_arn:
        print(
            f"FAIL: registry runtime {runtime_arn!r} != deployed {settings.runtime_arn!r}",
            file=sys.stderr,
        )
        return 1
    if not names:
        print("FAIL: agent descriptor has no tool list", file=sys.stderr)
        return 1
    if len(_tool_names(tools)) != 7:
        print("FAIL: tool catalog does not contain all seven Gateway tools", file=sys.stderr)
        return 1
    print(f"registry checks passed at {started.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
