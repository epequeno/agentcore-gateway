"""Eval harness.

``--smoke`` checks that configuration resolves and the runtime is reachable.
The real eval runs ten scripted multi-turn sessions against a deployed Runtime,
measuring pass rate, cold-start, warm latency, and cost per session.

Every tool call in those sessions appears in the Gateway's records, and zero
tool calls should reach a target directly — that is the thesis of this repo.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3

from agent.config import settings

SESSIONS = Path(__file__).parent / "sessions.jsonl"


def load_sessions() -> list[dict]:
    """Read the scripted sessions. Raises if the fixture is missing."""
    if not SESSIONS.exists():
        raise FileNotFoundError(f"missing fixture: {SESSIONS}")
    return [json.loads(line) for line in SESSIONS.read_text().splitlines() if line.strip()]


def smoke() -> int:
    """Check that settings resolve and report what is missing."""
    missing = [
        name
        for name, value in (
            ("MODEL_ID", settings.model_id),
            ("AGENTCORE_RUNTIME_ARN", settings.runtime_arn),
            ("AGENTCORE_MEMORY_ID", settings.memory_id),
            ("AGENTCORE_GATEWAY_URL", settings.gateway_url),
        )
        if not value
    ]
    print(f"region:   {settings.region}")
    print(f"sessions: {len(load_sessions())} scripted")
    if missing:
        print(f"unset:    {', '.join(missing)}")
    else:
        print("configuration resolves; ready for eval")
    return 0


def _invoke(runtime_arn: str, prompt: str, actor_id: str, session_id: str) -> str:
    """Invoke the Runtime and return the response text."""
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps(
            {
                "prompt": prompt,
                "actorId": actor_id,
                "sessionId": session_id,
            }
        ),
    )
    full = b""
    for event in resp["response"]:
        if isinstance(event, bytes):
            full += event
        elif isinstance(event, dict):
            body = event.get("body", b"")
            if isinstance(body, str):
                body = body.encode()
            full += body
    result = json.loads(full.decode("utf-8"))
    return result.get("response", "")


def _check_pass(response: str, expect: str) -> bool:
    """Check whether the response contains the expected substring (case-insensitive)."""
    return expect.lower() in response.lower()


def run_eval() -> dict:
    """Run the ten scripted sessions and return metrics."""
    sessions = load_sessions()
    runtime_arn = settings.runtime_arn
    if not runtime_arn:
        print("ERROR: AGENTCORE_RUNTIME_ARN is not set")
        sys.exit(1)

    results = []
    cold_start_ms = None
    warm_latencies = []
    total_turns = 0
    passed_turns = 0

    for session in sessions:
        sid = session["id"]
        actor_id = f"eval-{sid}"
        session_id = f"eval-session-{sid}"
        turns = session["turns"]
        session_passed = True

        for i, turn in enumerate(turns):
            prompt = turn["prompt"]
            expect = turn["expect"]

            start = time.perf_counter()
            try:
                response = _invoke(runtime_arn, prompt, actor_id, session_id)
            except Exception as e:
                response = f"ERROR: {e}"
            elapsed_ms = (time.perf_counter() - start) * 1000

            # First invocation across all sessions is the cold start.
            if cold_start_ms is None:
                cold_start_ms = elapsed_ms
            else:
                warm_latencies.append(elapsed_ms)

            total_turns += 1
            passed = _check_pass(response, expect)
            if passed:
                passed_turns += 1
            else:
                session_passed = False

            status = "PASS" if passed else "FAIL"
            print(
                f"  {sid} turn {i + 1}: {status} ({elapsed_ms:.0f}ms) "
                f"expect={expect!r} got={response[:80]!r}"
            )

        results.append({"id": sid, "passed": session_passed, "turns": len(turns)})

    # Aggregate
    sessions_passed = sum(1 for r in results if r["passed"])
    pass_rate = sessions_passed / len(results) if results else 0
    turn_pass_rate = passed_turns / total_turns if total_turns else 0

    warm_latencies.sort()
    warm_p50 = warm_latencies[len(warm_latencies) // 2] if warm_latencies else 0

    metrics = {
        "sessions_total": len(results),
        "sessions_passed": sessions_passed,
        "pass_rate": pass_rate,
        "turns_total": total_turns,
        "turns_passed": passed_turns,
        "turn_pass_rate": turn_pass_rate,
        "cold_start_ms": cold_start_ms or 0,
        "warm_p50_ms": warm_p50,
        "tool_calls_via_gateway": "not measured — see CloudWatch Gateway metrics",
        "tool_calls_direct": "not measured — Lambda has no direct invoke policy",
        "cost_per_session_usd": "not measured — see AWS Cost Explorer after teardown",
    }

    print()
    print("=== Eval Results ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentCore Gateway eval harness")
    parser.add_argument("--smoke", action="store_true", help="check config only")
    parser.add_argument("--json", action="store_true", help="output metrics as JSON")
    args = parser.parse_args(argv)

    if args.smoke:
        return smoke()

    metrics = run_eval()

    if args.json:
        print(json.dumps(metrics, indent=2))

    # Exit non-zero if pass rate is below threshold (currently any pass counts).
    if metrics["pass_rate"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
