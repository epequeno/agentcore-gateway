"""Measure the same task on AgentCore Harness and AgentCore Runtime.

Both arms use one model, one system prompt and the same seven-tool Gateway
catalog. The Runtime reaches the Gateway through ``MCPClient``; the Harness
receives a ``remote_mcp`` tool whose URL is the same Gateway ``/mcp`` endpoint
and whose headers carry a freshly minted Cognito access token.

The comparison is deliberately small: five measured sessions per arm after one
warm-up each. It is a case study, not a benchmark. Every row records wall time,
token usage and list-price inference cost; ``RESULTS.md`` carries the dated
summary.
"""

import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from agent.config import settings
from evals.identity import access_token, mcp_url

RESULTS = Path(__file__).parent.parent / "results"
TRIALS = 5

TASK_PROMPT = (
    "Use the CRM tool to look up the signed-in user's account. Report the "
    "account id and email exactly, then end with the word comparison-complete."
)

# AWS Bedrock list price for DeepSeek v3.2 in us-east-1, read from
# https://aws.amazon.com/bedrock/pricing/ (Standard on-demand, 2026-11-02).
INPUT_USD_PER_TOKEN = 0.62 / 1_000_000
OUTPUT_USD_PER_TOKEN = 1.85 / 1_000_000


def estimate_cost(usage: dict[str, Any]) -> float:
    """Return list-price inference cost for one measured invocation."""
    input_tokens = int(usage.get("inputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    return input_tokens * INPUT_USD_PER_TOKEN + output_tokens * OUTPUT_USD_PER_TOKEN


def _runtime_invoke(prompt: str, token: str, session_id: str, actor: str) -> dict[str, Any]:
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    started = time.perf_counter()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=settings.runtime_arn,
        runtimeSessionId=session_id,
        runtimeUserId=actor,
        payload=json.dumps(
            {
                "prompt": prompt,
                "authToken": token,
                "actorId": actor,
                "sessionId": session_id,
            }
        ),
    )
    stream = response["response"]
    raw = stream.read() if hasattr(stream, "read") else b"".join(stream)
    elapsed_ms = (time.perf_counter() - started) * 1000
    payload = json.loads(raw.decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return {
        "response": payload.get("response", ""),
        "usage": payload.get("usage", {}),
        "elapsed_ms": elapsed_ms,
    }


def _harness_invoke(prompt: str, token: str, session_id: str, actor: str) -> dict[str, Any]:
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    tools = [
        {
            "type": "remote_mcp",
            "name": "gateway",
            "config": {
                "remoteMcp": {
                    "url": mcp_url(),
                    "headers": {
                        "Authorization": f"Bearer {token}",
                        "x-amzn-bedrock-agentcore-policy-session-id": session_id,
                    },
                }
            },
        }
    ]
    started = time.perf_counter()
    response = client.invoke_harness(
        harnessArn=settings.harness_arn,
        runtimeSessionId=session_id,
        runtimeUserId=actor,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        tools=tools,
        allowedTools=["*"],
        maxIterations=6,
        timeoutSeconds=120,
    )
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    for event in response["stream"]:
        if "contentBlockDelta" in event:
            text = event["contentBlockDelta"].get("delta", {}).get("text")
            if text:
                text_parts.append(text)
        elif "metadata" in event:
            usage = event["metadata"].get("usage", {}) or {}
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "response": "".join(text_parts),
        "usage": usage,
        "elapsed_ms": elapsed_ms,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = sorted(row["elapsed_ms"] for row in rows)
    return {
        "n": len(rows),
        "pass": sum(1 for row in rows if row["passed"]),
        "p50_ms": latencies[len(latencies) // 2],
        "mean_ms": sum(latencies) / len(latencies),
        "input_tokens": sum(int(row["usage"].get("inputTokens") or 0) for row in rows),
        "output_tokens": sum(int(row["usage"].get("outputTokens") or 0) for row in rows),
        "cost_usd": sum(row["cost_usd"] for row in rows),
    }


def main() -> int:
    missing = [
        name
        for name, value in (
            ("AGENTCORE_RUNTIME_ARN", settings.runtime_arn),
            ("AGENTCORE_HARNESS_ARN", settings.harness_arn),
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

    token = access_token()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: dict[str, list[dict[str, Any]]] = {"runtime": [], "harness": []}

    # Warm both arms before timing; cold start is reported separately in the
    # Runtime results and is not part of this per-session comparison.
    for arm, invoke in (("runtime", _runtime_invoke), ("harness", _harness_invoke)):
        try:
            invoke(TASK_PROMPT, token, f"warm-{arm}-{uuid.uuid4().hex}", f"warm-{arm}")
            print(f"{arm}: warm-up complete")
        except Exception as exc:  # noqa: BLE001 - report the arm, keep the experiment
            print(f"{arm}: warm-up failed: {exc}")
            return 1

    for trial in range(TRIALS):
        for arm, invoke in (("runtime", _runtime_invoke), ("harness", _harness_invoke)):
            session_id = f"hosting-{arm}-{run_id}-{trial}-{uuid.uuid4().hex[:8]}"
            actor = f"hosting-{arm}"
            try:
                result = invoke(TASK_PROMPT, token, session_id, actor)
                text = result["response"]
                passed = "ACME-42" in text and "comparison-complete" in text.lower()
                row = {
                    **result,
                    "trial": trial + 1,
                    "passed": passed,
                    "cost_usd": estimate_cost(result["usage"]),
                }
            except Exception as exc:  # noqa: BLE001 - an arm failure is data
                row = {
                    "trial": trial + 1,
                    "response": f"ERROR: {exc}",
                    "usage": {},
                    "elapsed_ms": 0.0,
                    "passed": False,
                    "cost_usd": 0.0,
                }
            rows[arm].append(row)
            print(
                f"{arm} {trial + 1}: {'PASS' if row['passed'] else 'FAIL'} "
                f"{row['elapsed_ms']:.0f}ms "
                f"in={row['usage'].get('inputTokens', 0)} "
                f"out={row['usage'].get('outputTokens', 0)} "
                f"${row['cost_usd']:.6f}"
            )

    summary = {arm: _summarize(values) for arm, values in rows.items()}
    payload = {
        "run_id": run_id,
        "task": TASK_PROMPT,
        "model": settings.model_id,
        "price_usd_per_1m": {"input": 0.62, "output": 1.85},
        "rows": rows,
        "summary": summary,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "hosting.json").write_text(json.dumps(payload, indent=2, default=str))

    print("\narm       n  pass  p50 ms  mean ms  input tok  output tok  cost USD")
    for arm, item in summary.items():
        print(
            f"{arm:<8} {item['n']:>2}  {item['pass']:>4}  {item['p50_ms']:>6.0f}  "
            f"{item['mean_ms']:>7.0f}  {item['input_tokens']:>9}  "
            f"{item['output_tokens']:>10}  {item['cost_usd']:>8.6f}"
        )
    if all(item["pass"] == item["n"] for item in summary.values()):
        print("\nhosting comparison passed on both arms")
        return 0
    print("\nhosting comparison had failures", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
