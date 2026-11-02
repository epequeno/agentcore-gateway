"""Eval harness: scripted sessions, AgentCore batch evaluation, attribution.

Three modes, each one measurement:

``--smoke``      configuration resolves and the fixture parses.
``--run``        invoke every session against the deployed Runtime, then start
                 a batch evaluation over the spans those sessions produced.
``--phoenix``    invoke every session locally with spans exported to Phoenix,
                 for the cross-platform comparison in ``docs/EVAL-COMPARISON.md``.

The batch-evaluation source is CloudWatch Logs, not an inline session list:
AgentCore Evaluations discovers sessions from the spans ADOT delivered. With
unified telemetry those spans live in the agent's own log group, so one log
group is both the session source and the place the tool-call attribution query
runs.

A run is trustworthy only if two things hold, and both are printed:

1. the deterministic ``EchoFidelity`` evaluator PASSes every scenario except
   the seeded violation ``s11``, which must FAIL;
2. every ``execute_tool`` span in the window names the Gateway target's tool,
   so zero tool calls reach the Lambda directly.
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from agent.config import settings
from evals.identity import access_token

SESSIONS = Path(__file__).parent / "sessions.jsonl"
RESULTS = Path(__file__).parent.parent / "results"

SERVICE_NAME = "agentcore_gateway_agent.DEFAULT"
# Every tool the Gateway catalog exposes. A direct target call could not use
# these names, so this allowlist is what "via Gateway" means here.
GATEWAY_TOOLS = (
    "echo-target",
    "crm-mcp",
    "policy-quota",
    "openapi-status",
    "workflow",
    "toolbox",
    "x_amz_bedrock_agentcore_search",
)

BUILTIN_EVALUATORS = [
    "Builtin.Correctness",
    "Builtin.GoalSuccessRate",
    "Builtin.FailureAnalysis",
]


# ---------------------------------------------------------------------------
# Fixture and deployment shape
# ---------------------------------------------------------------------------


def load_sessions() -> list[dict]:
    """Read the scripted sessions. Raises if the fixture is missing."""
    if not SESSIONS.exists():
        raise FileNotFoundError(f"missing fixture: {SESSIONS}")
    return [json.loads(line) for line in SESSIONS.read_text().splitlines() if line.strip()]


def runtime_id(runtime_arn: str) -> str:
    """The Runtime id is the last segment of its ARN."""
    return runtime_arn.rstrip("/").rsplit("/", 1)[-1]


def log_group_for(runtime_arn: str) -> str:
    """Unified telemetry writes to ``/aws/bedrock-agentcore/runtimes/<id>-DEFAULT``."""
    return f"/aws/bedrock-agentcore/runtimes/{runtime_id(runtime_arn)}-DEFAULT"


def new_session_id(scenario_id: str) -> str:
    """Runtime session ids must be unique, stable within a session, and 33+ chars."""
    return f"eval-{scenario_id}-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def smoke() -> int:
    """Check that settings resolve and report what is missing."""
    sessions = load_sessions()
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
    print(f"sessions: {len(sessions)} scripted, {sum(len(s['turns']) for s in sessions)} turns")
    if settings.runtime_arn:
        print(f"log group: {log_group_for(settings.runtime_arn)}")
    print(f"evaluator: {settings.evaluator_arn or 'unset (AGENTCORE_EVALUATOR_ARN)'}")
    if missing:
        print(f"unset:    {', '.join(missing)}")
        return 1
    print("configuration resolves; ready for eval")
    return 0


def _invoke(runtime_arn: str, prompt: str, actor_id: str, session_id: str, auth_token: str) -> str:
    """Invoke the Runtime with the caller's user token and return the text."""
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        payload=json.dumps(
            {
                "prompt": prompt,
                "authToken": auth_token,
                "actorId": actor_id,
                "sessionId": session_id,
            }
        ),
    )
    stream = resp["response"]
    raw = stream.read() if hasattr(stream, "read") else b"".join(stream)
    result = json.loads(raw.decode("utf-8"))
    if "error" in result:
        raise RuntimeError(result["error"])
    return result.get("response", "")


def run_sessions(runtime_arn: str, auth_token: str) -> list[dict]:
    """Invoke every turn, recording responses and timings per scenario."""
    sessions = load_sessions()
    records: list[dict] = []

    for session in sessions:
        sid = new_session_id(session["id"])
        actor_id = f"eval-{session['id']}"
        turns = []
        started = datetime.now(UTC)
        for turn in session["turns"]:
            start = time.perf_counter()
            try:
                response = _invoke(runtime_arn, turn["prompt"], actor_id, sid, auth_token)
            except Exception as exc:
                response = f"ERROR: {exc}"
            elapsed_ms = (time.perf_counter() - start) * 1000
            turns.append(
                {
                    "prompt": turn["prompt"],
                    "expect": turn["expect"],
                    "response": response,
                    "elapsed_ms": elapsed_ms,
                }
            )
            status = "PASS" if turn["expect"].lower() in response.lower() else "FAIL"
            print(f"  {session['id']} turn {len(turns)}: {status} ({elapsed_ms:.0f}ms)")
        records.append(
            {
                "scenario_id": session["id"],
                "session_id": sid,
                "started_at": started,
                "ended_at": datetime.now(UTC),
                "seeded_violation": bool(session.get("seeded_violation")),
                "assertions": session.get("assertions", []),
                "turns": turns,
            }
        )
    return records


def _session_metadata(records: list[dict]) -> list[dict]:
    """Ground truth in the shape StartBatchEvaluation expects."""
    metadata = []
    for record in records:
        inline: dict[str, Any] = {
            "turns": [
                {
                    "input": {"prompt": turn["prompt"]},
                    "expectedResponse": {"text": turn["expect"]},
                }
                for turn in record["turns"]
            ]
        }
        if record["assertions"]:
            inline["assertions"] = [{"text": text} for text in record["assertions"]]
        entry = {
            "sessionId": record["session_id"],
            "testScenarioId": record["scenario_id"],
            "groundTruth": {"inline": inline},
        }
        metadata.append(entry)
    return metadata


def evaluator_id(value: str) -> str:
    """The API wants the evaluator id (``Name-abc1234567``), not its ARN."""
    return value.rsplit("/", 1)[-1] if "/" in value else value


def _start_batch_evaluation(records: list[dict], log_group: str) -> dict:
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    evaluators = list(BUILTIN_EVALUATORS)
    if settings.evaluator_arn:
        evaluators.append(evaluator_id(settings.evaluator_arn))
    if settings.rubric_evaluator_arn:
        evaluators.append(evaluator_id(settings.rubric_evaluator_arn))

    return client.start_batch_evaluation(
        batchEvaluationName=f"gateway_eval_{uuid.uuid4().hex[:8]}",
        evaluators=[{"evaluatorId": evaluator} for evaluator in evaluators],
        dataSourceConfig={
            "cloudWatchLogs": {
                "serviceNames": [SERVICE_NAME],
                "logGroupNames": [log_group],
                "filterConfig": {
                    "sessionIds": [record["session_id"] for record in records],
                    "timeRange": {
                        "startTime": min(r["started_at"] for r in records).isoformat(),
                        "endTime": datetime.now(UTC).isoformat(),
                    },
                },
            }
        },
        evaluationMetadata={"sessionMetadata": _session_metadata(records)},
        clientToken=str(uuid.uuid4()),
    )


def _poll(batch_id: str, timeout_s: int = 1800, interval_s: int = 15) -> dict:
    client = boto3.client("bedrock-agentcore", region_name=settings.region)
    deadline = time.monotonic() + timeout_s
    while True:
        response = client.get_batch_evaluation(batchEvaluationId=batch_id)
        status = response["status"]
        print(f"  batch {batch_id}: {status}")
        if status in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED"}:
            return response
        if time.monotonic() > deadline:
            raise TimeoutError(f"batch {batch_id} still {status} after {timeout_s}s")
        time.sleep(interval_s)


def _event_value(event: dict, key: str) -> Any:
    """Read an evaluation field from either the event or its attributes."""
    if key in event:
        return event[key]
    return (event.get("attributes") or {}).get(key)


def _summarise_events(events: list[dict]) -> dict:
    """Group per-turn evaluation events into scores and seeded-verdict rows."""
    scores: dict[str, list[float]] = {}
    verdicts: dict[str, dict] = {}
    explanations: list[str] = []

    for event in events:
        name = _event_value(event, "gen_ai.evaluation.name")
        if not name:
            continue
        value = _event_value(event, "gen_ai.evaluation.score.value")
        label = _event_value(event, "gen_ai.evaluation.score.label")
        session = (
            event.get("session.id")
            or (event.get("attributes") or {}).get("session.id")
            or event.get("sessionId")
        )
        explanation = _event_value(event, "gen_ai.evaluation.explanation")

        if isinstance(value, (int, float)):
            scores.setdefault(name, []).append(float(value))
        if name == "EchoFidelity" and session:
            verdicts[session] = {"label": label, "value": value, "explanation": explanation}
        if explanation:
            explanations.append(f"{name}: {explanation}")

    return {
        "scores": {
            name: {
                "mean": sum(values) / len(values),
                "n": len(values),
                "min": min(values),
                "max": max(values),
            }
            for name, values in scores.items()
        },
        "echo_fidelity_verdicts": verdicts,
        "explanations": explanations,
    }


def _tool_attribution(log_group: str, records: list[dict]) -> dict:
    """Count execute_tool spans and their names in the session window."""
    logs = boto3.client("logs", region_name=settings.region)
    start = int(min(r["started_at"] for r in records).timestamp() * 1000)
    end = int(time.time() * 1000)
    session_filter = ", ".join(f'"{r["session_id"]}"' for r in records)
    query = (
        "fields @timestamp, @message"
        ' | filter scope.name = "strands.telemetry.tracer"'
        ' | filter attributes.gen_ai.operation.name = "execute_tool"'
        f" | filter attributes.session.id in [{session_filter}]"
        " | stats count() by attributes.gen_ai.tool.name"
    )
    response = logs.start_query(
        logGroupName=log_group, startTime=start, endTime=end, queryString=query
    )
    query_id = response["queryId"]
    for _ in range(60):
        result = logs.get_query_results(queryId=query_id)
        if result["status"] in {"Complete", "Failed", "Cancelled"}:
            break
        time.sleep(2)

    tools: dict[str, int] = {}
    for row in result["results"]:
        cells = {cell["field"]: cell["value"] for cell in row}
        name = cells.get("attributes.gen_ai.tool.name", "")
        if name:
            tools[name] = int(float(cells.get("count()", 0)))

    total = sum(tools.values())
    direct = [name for name in tools if not name.startswith(GATEWAY_TOOLS)]
    return {
        "total": total,
        "by_tool": tools,
        "direct": direct,
        "all_via_gateway": total > 0 and not direct,
    }


def _count_spans(log_group: str, records: list[dict], operation: str) -> dict[str, int]:
    logs = boto3.client("logs", region_name=settings.region)
    start = int(min(r["started_at"] for r in records).timestamp() * 1000)
    end = int(time.time() * 1000)
    session_filter = ", ".join(f'"{r["session_id"]}"' for r in records)
    query = (
        "fields @timestamp, @message"
        ' | filter scope.name = "strands.telemetry.tracer"'
        f' | filter attributes.gen_ai.operation.name = "{operation}"'
        f" | filter attributes.session.id in [{session_filter}]"
        " | stats count() by attributes.session.id"
    )
    response = logs.start_query(
        logGroupName=log_group, startTime=start, endTime=end, queryString=query
    )
    query_id = response["queryId"]
    for _ in range(60):
        result = logs.get_query_results(queryId=query_id)
        if result["status"] in {"Complete", "Failed", "Cancelled"}:
            break
        time.sleep(2)
    counts = {}
    for row in result["results"]:
        cells = {cell["field"]: cell["value"] for cell in row}
        session = cells.get("attributes.session.id")
        if session:
            counts[session] = int(float(cells.get("count()", 0)))
    return counts


def wait_for_spans(log_group: str, records: list[dict], timeout_s: int = 600) -> None:
    """Do not start evaluation until every turn has an invoke_agent span.

    CloudWatch ingestion lags the invocation; a missing trace makes the service
    reject a ground-truth context, which is a partial-credit run, not a result.
    """
    expected = {record["session_id"]: len(record["turns"]) for record in records}
    deadline = time.monotonic() + timeout_s
    while True:
        counts = _count_spans(log_group, records, "invoke_agent")
        missing = {
            session: (counts.get(session, 0), want)
            for session, want in expected.items()
            if counts.get(session, 0) < want
        }
        print(f"  spans ingested: {len(expected) - len(missing)}/{len(expected)} sessions complete")
        if not missing:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"spans still missing after {timeout_s}s: {missing}")
        time.sleep(30)


def run(runtime_arn: str) -> dict:
    log_group = log_group_for(runtime_arn)
    print(f"invoking {len(load_sessions())} sessions against {runtime_arn}")
    records = run_sessions(runtime_arn, access_token())

    print("waiting for span ingestion")
    wait_for_spans(log_group, records)

    latencies = sorted(turn["elapsed_ms"] for record in records for turn in record["turns"])
    substring_pass = sum(
        1
        for record in records
        for turn in record["turns"]
        if turn["expect"].lower() in turn["response"].lower()
    )
    total_turns = len(latencies)

    print(f"starting batch evaluation over {log_group}")
    started = _start_batch_evaluation(records, log_group)
    batch_id = started["batchEvaluationId"]
    response = _poll(batch_id)

    events = []
    output_config = (response.get("outputConfig") or {}).get("cloudWatchConfig")
    if output_config:
        from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_models import (
            BatchEvaluationResult,
            CloudWatchOutputDataConfig,
        )
        from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_runner import (
            BatchEvaluationRunner,
        )

        try:
            result = BatchEvaluationResult(
                batch_evaluation_id=batch_id,
                batch_evaluation_arn=response["batchEvaluationArn"],
                batch_evaluation_name=response["batchEvaluationName"],
                status=response["status"],
                created_at=response["createdAt"],
                output_data_config=CloudWatchOutputDataConfig(
                    log_group_name=output_config["logGroupName"],
                    log_stream_name=output_config["logStreamName"],
                ),
            )
            events = BatchEvaluationRunner(region=settings.region).fetch_evaluation_events(result)
        except Exception as exc:
            print(f"  per-turn events unavailable: {exc}")

    summary = _summarise_events(events)
    attribution = _tool_attribution(log_group, records)

    metrics = {
        "runtime_arn": runtime_arn,
        "log_group": log_group,
        "batch_evaluation_id": batch_id,
        "batch_status": response.get("status"),
        "sessions_total": len(records),
        "turns_total": total_turns,
        # The Runtime is normally already warm by the time the harness runs, so
        # this is the first invocation of this run, not a cold start. A cold
        # start needs a fresh deploy and one immediate invoke.
        "first_invocation_ms": latencies[0] if latencies else 0,
        "warm_p50_ms": latencies[len(latencies) // 2] if len(latencies) > 1 else 0,
        "substring_pass_rate": substring_pass / total_turns if total_turns else 0,
        "service_summary": response.get("evaluationResults"),
        "evaluator_scores": summary["scores"],
        "echo_fidelity_verdicts": summary["echo_fidelity_verdicts"],
        "insights": response.get("insightResults"),
        "sessions": [
            {
                "scenario_id": record["scenario_id"],
                "session_id": record["session_id"],
                "seeded_violation": record["seeded_violation"],
                "turns": record["turns"],
            }
            for record in records
        ],
        "events": events,
        "tool_calls": attribution,
        "seeded_violation_failed": _seeded_failed(records, summary["echo_fidelity_verdicts"]),
        "measured_at": datetime.now(UTC).isoformat(),
    }

    print()
    print("=== Eval results ===")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    if metrics["seeded_violation_failed"] is False:
        print("WARNING: the seeded violation did not fail; the evaluator is not proving anything.")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"batch-{batch_id}.json"
    out.write_text(json.dumps(metrics, indent=2, default=str) + "\n")
    print(f"wrote {out}")
    return metrics


def _seeded_failed(records: list[dict], verdicts: dict) -> bool | None:
    seeded = [r for r in records if r["seeded_violation"]]
    if not seeded or not verdicts:
        return None
    record = seeded[0]
    verdict = verdicts.get(record["session_id"])
    if not verdict:
        return None
    return str(verdict.get("label", "")).upper() == "FAIL"


def run_phoenix() -> int:
    """Run the same sessions locally with spans exported to Phoenix."""
    endpoint = os.environ.get("PHOENIX_OTLP_ENDPOINT")
    if not endpoint:
        print("PHOENIX_OTLP_ENDPOINT is not set (see ~/code/observability-stack)")
        return 2
    if not settings.gateway_url or not settings.memory_id:
        print("AGENTCORE_GATEWAY_URL and AGENTCORE_MEMORY_ID must be set")
        return 2

    # The entrypoint reads settings and installs telemetry at import time.
    from agent import app as agent_app

    sessions = load_sessions()
    records = []
    for session in sessions:
        sid = new_session_id(session["id"])
        turns = []
        for turn in session["turns"]:
            result = agent_app.handler(
                {
                    "prompt": turn["prompt"],
                    "actorId": f"phoenix-{session['id']}",
                    "sessionId": sid,
                }
            )
            response = result.get("response", result.get("error", ""))
            turns.append({"prompt": turn["prompt"], "expect": turn["expect"], "response": response})
            print(f"  {session['id']} turn {len(turns)}: {response[:80]!r}")
        records.append({"scenario_id": session["id"], "session_id": sid, "turns": turns})

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"phoenix-sessions-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(records, indent=2) + "\n")
    print(f"wrote {out}; give the collector a few seconds, then run evals/phoenix.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentCore Gateway eval harness")
    parser.add_argument("--smoke", action="store_true", help="check config only")
    parser.add_argument("--run", action="store_true", help="invoke and batch-evaluate")
    parser.add_argument("--phoenix", action="store_true", help="run locally with Phoenix export")
    parser.add_argument("--json", action="store_true", help="output metrics as JSON")
    args = parser.parse_args(argv)

    if args.smoke:
        return smoke()

    if args.phoenix:
        return run_phoenix()

    if not settings.runtime_arn:
        print("ERROR: AGENTCORE_RUNTIME_ARN is not set")
        return 1

    metrics = run(settings.runtime_arn)
    if args.json:
        print(json.dumps(metrics, indent=2, default=str))

    ok = metrics["batch_status"] in {"COMPLETED", "COMPLETED_WITH_ERRORS"}
    ok = ok and metrics["tool_calls"]["all_via_gateway"]
    ok = ok and metrics["seeded_violation_failed"] is not False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
