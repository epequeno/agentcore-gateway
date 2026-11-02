"""Score the same sessions in Phoenix and compare with AgentCore Evaluations.

Phoenix is the second reader of the same Strands spans: ``just eval-phoenix``
runs the sessions locally with ``PHOENIX_OTLP_ENDPOINT`` set, then this script
pulls the session records, scores every turn with Phoenix's evaluator SDK, and
pairs those verdicts with the AgentCore batch-evaluation events.

Two evaluator pairs, both with the judge model held constant:

- prebuilt correctness — ``Builtin.Correctness`` vs Phoenix's
  ``CorrectnessEvaluator`` (different prompts, same model);
- the controlled rubric — the ``GatewayRubric`` evaluator's instructions reused
  verbatim by a Phoenix ``ClassificationEvaluator``.

Faithfulness is deliberately absent: the only tool is ``echo``, so there is no
retrieved context to be faithful to. See ``docs/DECISIONS.md``.

Run: ``uv run --extra evals python -m evals.phoenix`` (or ``just phoenix-compare``).
"""

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.config import settings

RESULTS = Path(__file__).parent.parent / "results"
PROJECT = "agentcore-gateway"

# AgentCore-side evaluator names as they appear in evaluation events.
AGENTCORE_CORRECTNESS = "Builtin.Correctness"
AGENTCORE_RUBRIC = "GatewayRubric"

# Bedrock list price for deepseek.v3.2, checked 2026-11-01.
# https://aws.amazon.com/bedrock/pricing/
INPUT_USD_PER_MTOKEN = 0.62
OUTPUT_USD_PER_MTOKEN = 1.85

RUBRIC_TEMPLATE = """\
You are grading one assistant turn from a tool-using agent.

Conversation context:
{context}

Assistant turn under evaluation:
{assistant_turn}

The agent's only tool echoes its input unchanged. Grade PASS when the assistant
turn directly answers the user's request and, if the user asked for the echo
tool, reports the echoed text without contradicting the tool output or inventing
a result. Grade FAIL otherwise.

Answer with the single label PASS or FAIL.
"""


def _latest(pattern: str) -> Path:
    files = list(RESULTS.glob(pattern))
    if not files:
        raise FileNotFoundError(f"no {pattern} in {RESULTS}")
    # Batch ids are random, so filename order is not run order.
    return max(files, key=lambda path: path.stat().st_mtime)


def load_local_sessions(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def phoenix_span_counts(project: str = PROJECT) -> dict[str, int]:
    """Count spans per session in Phoenix, proving it consumed the traces."""
    url = f"http://localhost:6006/v1/projects/{project}/spans?limit=1000"
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - localhost
        payload = json.load(response)
    counts: dict[str, int] = {}
    for span in payload.get("data", []):
        session = (span.get("attributes") or {}).get("session.id")
        if session:
            counts[session] = counts.get(session, 0) + 1
    return counts


def load_agentcore(path: Path) -> dict:
    return json.loads(path.read_text())


def _events_for(metrics: dict, evaluator: str) -> dict[str, list[dict]]:
    """Group per-turn events for one evaluator by session id, in arrival order."""
    grouped: dict[str, list[dict]] = {}
    for event in metrics.get("events", []):
        name = event.get("gen_ai.evaluation.name") or (event.get("attributes") or {}).get(
            "gen_ai.evaluation.name"
        )
        if name != evaluator:
            continue
        session = event.get("session.id") or (event.get("attributes") or {}).get("session.id")
        if not session:
            continue
        grouped.setdefault(session, []).append(event)
    return grouped


def _agentcore_verdict(event: dict) -> bool | None:
    value = event.get("gen_ai.evaluation.score.value") or (event.get("attributes") or {}).get(
        "gen_ai.evaluation.score.value"
    )
    if isinstance(value, (int, float)):
        return value >= 0.5
    label = (
        event.get("gen_ai.evaluation.score.label")
        or (event.get("attributes") or {}).get("gen_ai.evaluation.score.label")
        or ""
    ).upper()
    if label in {"PASS", "CORRECT", "TRUE"}:
        return True
    if label in {"FAIL", "INCORRECT", "FALSE"}:
        return False
    return None


def _phoenix_verdict(score) -> bool | None:
    if score is None:
        return None
    value = getattr(score, "score", None)
    if isinstance(value, (int, float)):
        return value >= 0.5
    label = (getattr(score, "label", None) or "").upper()
    if label in {"PASS", "CORRECT", "TRUE"}:
        return True
    if label in {"FAIL", "INCORRECT", "FALSE"}:
        return False
    return None


def cohens_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa over paired binary verdicts."""
    n = len(pairs)
    if n == 0:
        return None
    a = sum(1 for left, right in pairs if left and right)
    b = sum(1 for left, right in pairs if left and not right)
    c = sum(1 for left, right in pairs if not left and right)
    d = n - a - b - c
    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    if pe == 1:
        return None
    return (po - pe) / (1 - pe)


def agreement(pairs: list[tuple[bool, bool]]) -> float | None:
    if not pairs:
        return None
    return sum(1 for left, right in pairs if left == right) / len(pairs)


class _UsageMeter:
    """Collect judge tokens from litellm, which backs Phoenix's Bedrock client."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def install(self) -> None:
        import litellm
        from litellm.integrations.custom_logger import CustomLogger

        meter = self

        class _Logger(CustomLogger):
            def log_success_event(self, kwargs, response_obj, start_time, end_time):  # type: ignore[override]
                usage = getattr(response_obj, "usage", None) or kwargs.get("usage")
                if usage is None:
                    return
                meter.calls += 1
                meter.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                meter.completion_tokens += getattr(usage, "completion_tokens", 0) or 0

        litellm.callbacks = [_Logger()]

    def cost_usd(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * INPUT_USD_PER_MTOKEN
            + self.completion_tokens / 1_000_000 * OUTPUT_USD_PER_MTOKEN
        )


def build_judges():
    """Phoenix-side judges: prebuilt correctness and the controlled rubric."""
    from phoenix.evals import LLM, ClassificationEvaluator
    from phoenix.evals.metrics.correctness import CorrectnessEvaluator

    meter = _UsageMeter()
    meter.install()
    llm = LLM(provider="bedrock", model=settings.judge_model_id)
    correctness = CorrectnessEvaluator(llm=llm, temperature=0.0)
    rubric = ClassificationEvaluator(
        name=AGENTCORE_RUBRIC,
        llm=llm,
        prompt_template=RUBRIC_TEMPLATE,
        choices={"PASS": 1.0, "FAIL": 0.0},
        temperature=0.0,
    )
    return (correctness, rubric), meter


def score_in_phoenix(records: list[dict], judges) -> list[dict]:
    """One verdict row per turn, in session/turn order."""
    correctness, rubric = judges
    rows = []
    for record in records:
        for index, turn in enumerate(record["turns"]):
            cp = correctness.evaluate({"input": turn["prompt"], "output": turn["response"]})
            rp = rubric.evaluate({"context": turn["prompt"], "assistant_turn": turn["response"]})
            rows.append(
                {
                    "scenario_id": record["scenario_id"],
                    "session_id": record["session_id"],
                    "turn": index,
                    "prompt": turn["prompt"],
                    "correctness": _phoenix_verdict(cp[0]) if cp else None,
                    "rubric": _phoenix_verdict(rp[0]) if rp else None,
                    "explanations": {
                        "correctness": getattr(cp[0], "explanation", None) if cp else None,
                        "rubric": getattr(rp[0], "explanation", None) if rp else None,
                    },
                }
            )
    return rows


def _events_by_scenario(metrics: dict, evaluator: str) -> dict[str, list[dict]]:
    """Regroup events by scenario id.

    The two runs are separate executions, so session ids differ. The fixed
    input is the scenario and turn index, not the session id.
    """
    session_to_scenario = {r["session_id"]: r["scenario_id"] for r in metrics["sessions"]}
    grouped: dict[str, list[dict]] = {}
    for session, events in _events_for(metrics, evaluator).items():
        scenario = session_to_scenario.get(session)
        if scenario:
            grouped[scenario] = events
    return grouped


def prompts_by_trace(log_group: str, trace_ids: list[str]) -> dict[str, str]:
    """Recover the user prompt for each AgentCore trace.

    Event order is not guaranteed to match turn order, so pair by the prompt
    string — the only field the two runs genuinely share.
    """
    if not trace_ids:
        return {}
    import boto3

    logs = boto3.client("logs", region_name=settings.region)
    timestamp = ", ".join(f'"{trace}"' for trace in trace_ids)
    query = (
        "fields @timestamp, @message"
        ' | filter attributes.gen_ai.operation.name = "invoke_agent"'
        f" | filter traceId in [{timestamp}]"
        " | sort @timestamp asc"
    )
    now = int(datetime.now(UTC).timestamp() * 1000)
    response = logs.start_query(
        logGroupName=log_group,
        startTime=now - 86_400_000,
        endTime=now,
        queryString=query,
    )
    for _ in range(60):
        result = logs.get_query_results(queryId=response["queryId"])
        if result["status"] in {"Complete", "Failed", "Cancelled"}:
            break
        time.sleep(2)

    prompts: dict[str, str] = {}
    for row in result.get("results", []):
        cells = {cell["field"]: cell["value"] for cell in row}
        document = json.loads(cells.get("@message", "{}"))
        trace_id = document.get("traceId")
        for item in document.get("events") or []:
            if item.get("name") != "gen_ai.user.message":
                continue
            content = (item.get("attributes") or {}).get("content", "")
            prompts[trace_id] = _as_text(content)
            break
    return prompts


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            return _as_text(json.loads(value))
        except (ValueError, TypeError):
            return value
    if isinstance(value, list):
        return "".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        if "text" in value:
            return _as_text(value["text"])
        for key in ("stringValue", "content"):
            if key in value:
                return _as_text(value[key])
    return str(value or "")


def pair_agentcore(metrics: dict, phoenix_rows: list[dict]) -> dict[str, Any]:
    """Pair each turn's verdicts by (scenario id, prompt)."""
    agents = {
        AGENTCORE_CORRECTNESS: _events_by_scenario(metrics, AGENTCORE_CORRECTNESS),
        AGENTCORE_RUBRIC: _events_by_scenario(metrics, AGENTCORE_RUBRIC),
    }
    trace_ids = [
        event.get("attributes", {}).get("gen_ai.response.id")
        for events in agents.values()
        for items in events.values()
        for event in items
        if event.get("attributes", {}).get("gen_ai.response.id")
    ]
    trace_prompts = prompts_by_trace(metrics["log_group"], trace_ids)
    pairs: dict[str, list[dict]] = {AGENTCORE_CORRECTNESS: [], AGENTCORE_RUBRIC: []}

    for row in phoenix_rows:
        scenario = row["scenario_id"]
        for name, grouped in agents.items():
            events = grouped.get(scenario, [])
            event = next(
                (
                    item
                    for item in events
                    if trace_prompts.get(item.get("attributes", {}).get("gen_ai.response.id"))
                    == row["prompt"]
                ),
                None,
            )
            if event is None:
                continue
            left = _agentcore_verdict(event)
            right = row["correctness"] if name == AGENTCORE_CORRECTNESS else row["rubric"]
            if left is None or right is None:
                continue
            pairs[name].append(
                {
                    "scenario_id": scenario,
                    "session_id": row["session_id"],
                    "turn": row["turn"],
                    "agentcore": left,
                    "phoenix": right,
                    "phoenix_explanation": row["explanations"][
                        "correctness" if name == AGENTCORE_CORRECTNESS else "rubric"
                    ],
                }
            )

    report = {}
    for name, rows in pairs.items():
        verdicts = [(row["agentcore"], row["phoenix"]) for row in rows]
        disagreements = [row for row in rows if row["agentcore"] != row["phoenix"]]
        report[name] = {
            "n": len(rows),
            "agreement": agreement(verdicts),
            "kappa": cohens_kappa(verdicts),
            "disagreements": disagreements,
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phoenix vs AgentCore evaluator comparison")
    parser.add_argument("--sessions", type=Path, help="results/phoenix-sessions-*.json")
    parser.add_argument("--agentcore", type=Path, help="results/batch-*.json")
    args = parser.parse_args(argv)

    sessions_path = args.sessions or _latest("phoenix-sessions-*.json")
    agentcore_path = args.agentcore or _latest("batch-*.json")
    records = load_local_sessions(sessions_path)
    metrics = load_agentcore(agentcore_path)

    spans = phoenix_span_counts()
    missing_spans = [r["session_id"] for r in records if r["session_id"] not in spans]
    if missing_spans:
        raise SystemExit(
            f"Phoenix has no spans for {len(missing_spans)} session(s): {missing_spans}"
        )

    # Score the AgentCore run's own transcripts in Phoenix. Two independent
    # runs produce different outputs, and a judge comparison must hold inputs
    # fixed; the local run's role is proving Phoenix consumes the same spans.
    judges, meter = build_judges()
    phoenix_rows = score_in_phoenix(metrics["sessions"], judges)
    report = pair_agentcore(metrics, phoenix_rows)
    cost = meter.cost_usd()
    per_case = cost / len(phoenix_rows) if phoenix_rows else None

    output = {
        "project": PROJECT,
        "judge_model": settings.judge_model_id,
        "spans_file": str(sessions_path),
        "scored_from": "AgentCore run transcripts (results/batch-*.json)",
        "agentcore_file": str(agentcore_path),
        "measured_at": datetime.now(UTC).isoformat(),
        "pairs": report,
        "price_per_mtoken_usd": {
            "input": INPUT_USD_PER_MTOKEN,
            "output": OUTPUT_USD_PER_MTOKEN,
        },
        "turns_scored": len(phoenix_rows),
        "phoenix_spans": {
            "sessions_with_spans": len(spans),
            "spans": sum(spans.values()),
        },
        "phoenix_judge": {
            "calls": meter.calls,
            "prompt_tokens": meter.prompt_tokens,
            "completion_tokens": meter.completion_tokens,
            "cost_usd": cost,
            "cost_per_case_usd": per_case,
        },
        "agentcore_judge_cost": "not exposed by the evaluation API",
    }

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"phoenix-comparison-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(output, indent=2, default=str) + "\n")

    print("| Pair | n | Agreement | Cohen's κ | Disagreements |")
    print("|---|---|---|---|---|")
    for name, row in report.items():
        kappa = "n/a" if row["kappa"] is None else f"{row['kappa']:.3f}"
        agr = "n/a" if row["agreement"] is None else f"{row['agreement']:.0%}"
        print(f"| {name} | {row['n']} | {agr} | {kappa} | {len(row['disagreements'])} |")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
