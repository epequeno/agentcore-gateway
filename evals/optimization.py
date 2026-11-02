"""Run the Optimization prompt A/B test through the Runtime front door.

The control and treatment are two immutable configuration-bundle versions that
differ only in ``system_prompt``. The front door Gateway assigns each session a
variant and propagates the bundle reference in W3C baggage; the Runtime reads it
in a ``BeforeModelCallEvent`` hook. An online evaluation config scores both arms
with the same built-in evaluators.

The A/B test itself is a service resource outside CloudFormation. This script
stops and deletes it, and deletes both bundle versions, on every exit path.
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
from agent.prompts import PROMPTS
from evals.frontdoor import invoke_runtime
from evals.identity import access_token

RESULTS = Path(__file__).parent.parent / "results"
ATTEMPTS = 20
POLL_SECONDS = 30
MAX_WAIT_SECONDS = 20 * 60

PROMPTS_FOR_SESSIONS = [
    "Use the CRM tool to look up the signed-in user's account and report it.",
    "What is the signed-in user's account id and email? Use the CRM tool.",
    "Check the current gateway status with the status tool.",
    "Use the policy quota tool to approve spending 25 credits.",
    "Use the echo tool to echo the phrase prompt-ab-ready.",
    "Run a short Python expression in the code tool and report the result.",
    "Browse https://example.com and report the page title.",
    'Start the workflow once with the input {"who":"ab"}.',
    "Use the CRM tool, then state the account and its plan.",
    "Answer with the signed-in user's identity after checking the CRM tool.",
]


def _create_bundle(client: Any, name: str, prompt: str, runtime_arn: str) -> dict[str, Any]:
    return client.create_configuration_bundle(
        bundleName=name,
        description="Prompt A/B variant for the Runtime comparison",
        components={
            runtime_arn: {
                "configuration": {
                    "system_prompt": prompt,
                }
            }
        },
        branchName="main",
        commitMessage=f"prompt variant: {name}",
        clientToken=str(uuid.uuid4()),
    )


def _delete_bundles(client: Any, bundles: list[str]) -> None:
    for bundle_id in bundles:
        try:
            client.delete_configuration_bundle(bundleId=bundle_id)
            print(f"deleted bundle {bundle_id}")
        except Exception as exc:  # noqa: BLE001 - best-effort teardown
            print(f"bundle cleanup failed for {bundle_id}: {exc}", file=sys.stderr)


def _stop_and_delete(client: Any, ab_test_id: str) -> None:
    try:
        client.update_ab_test(abTestId=ab_test_id, executionStatus="STOPPED")
    except Exception as exc:  # noqa: BLE001 - already stopped is fine
        print(f"stop request: {exc}")
    for _ in range(20):
        try:
            state = client.get_ab_test(abTestId=ab_test_id)
        except Exception:  # noqa: BLE001 - deleted is the goal
            return
        if state.get("status") in {"STOPPED", "DELETED", "FAILED"}:
            break
        time.sleep(5)
    last_error: Exception | str = "unknown"
    for _ in range(12):
        try:
            client.delete_ab_test(abTestId=ab_test_id)
            print(f"deleted A/B test {ab_test_id}")
            return
        except Exception as exc:  # noqa: BLE001 - the state can still be UPDATING
            last_error = exc
            time.sleep(5)
    print(f"A/B cleanup failed for {ab_test_id}: {last_error}", file=sys.stderr)


def _run_sessions(token: str, run_id: str) -> list[dict[str, Any]]:
    rows = []
    for index in range(ATTEMPTS):
        prompt = PROMPTS_FOR_SESSIONS[index % len(PROMPTS_FOR_SESSIONS)]
        session_id = f"prompt-ab-{run_id}-{index}-{uuid.uuid4().hex}"
        try:
            response = invoke_runtime(
                prompt=prompt,
                token=token,
                session_id=session_id,
                actor_id="prompt-ab",
            )
            rows.append(
                {
                    "session": session_id,
                    "prompt": prompt,
                    "response": response.get("response", ""),
                    "usage": response.get("usage", {}),
                }
            )
            print(f"session {index + 1}/{ATTEMPTS}: ok")
        except Exception as exc:  # noqa: BLE001 - a failed session is evidence
            rows.append(
                {
                    "session": session_id,
                    "prompt": prompt,
                    "response": f"ERROR: {exc}",
                    "usage": {},
                }
            )
            print(f"session {index + 1}/{ATTEMPTS}: ERROR {exc}")
    return rows


def _poll_results(client: Any, ab_test_id: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    latest = None
    while time.monotonic() < deadline:
        latest = client.get_ab_test(abTestId=ab_test_id)
        results = latest.get("results") or {}
        metrics = results.get("evaluatorMetrics") or []
        print(
            f"status={latest.get('status')} execution={latest.get('executionStatus')} "
            f"metrics={len(metrics)}"
        )
        if metrics:
            complete = all(
                (metric.get("controlStats") or {}).get("sampleSize", 0) > 0
                and all(
                    (variant.get("sampleSize") or 0) > 0
                    for variant in (metric.get("variantResults") or [])
                )
                for metric in metrics
            )
            counts = [
                (metric.get("controlStats") or {}).get("sampleSize", 0)
                + sum(
                    (variant.get("sampleSize") or 0)
                    for variant in (metric.get("variantResults") or [])
                )
                for metric in metrics
            ]
            if complete and min(counts) >= ATTEMPTS:
                return latest
        time.sleep(POLL_SECONDS)
    return latest


def main() -> int:
    missing = [
        name
        for name, value in (
            ("AGENTCORE_RUNTIME_ARN", settings.runtime_arn),
            ("AGENTCORE_FRONT_GATEWAY_ARN", settings.front_gateway_arn),
            ("AGENTCORE_ONLINE_EVAL_ARN", settings.online_eval_arn),
            ("AGENTCORE_AB_TEST_ROLE_ARN", settings.ab_test_role_arn),
            ("COGNITO_USER_POOL_ID", settings.user_pool_id),
            ("COGNITO_CLIENT_ID", settings.cognito_client_id),
            ("IDENTITY_TEST_PASSWORD", settings.identity_password),
        )
        if not value
    ]
    if missing:
        print(f"set {', '.join(missing)}", file=sys.stderr)
        return 2

    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    suffix = run_id[-8:]
    bundles: list[str] = []
    ab_test_id = ""
    control = boto3.client("bedrock-agentcore-control", region_name=settings.region)
    data = boto3.client("bedrock-agentcore", region_name=settings.region)
    token = access_token()

    try:
        control_bundle = _create_bundle(
            control,
            f"agentcore_prompt_control_{suffix}",
            PROMPTS["control"],
            settings.runtime_arn,
        )
        bundles.append(control_bundle["bundleId"])
        treatment_bundle = _create_bundle(
            control,
            f"agentcore_prompt_treatment_{suffix}",
            PROMPTS["treatment"],
            settings.runtime_arn,
        )
        bundles.append(treatment_bundle["bundleId"])

        created = data.create_ab_test(
            name=f"agentcore_prompt_ab_{suffix}",
            description="Control vs treatment system prompt through the front door",
            gatewayArn=settings.front_gateway_arn,
            variants=[
                {
                    "name": "C",
                    "weight": 50,
                    "variantConfiguration": {
                        "configurationBundle": {
                            "bundleArn": control_bundle["bundleArn"],
                            "bundleVersion": control_bundle["versionId"],
                        }
                    },
                },
                {
                    "name": "T1",
                    "weight": 50,
                    "variantConfiguration": {
                        "configurationBundle": {
                            "bundleArn": treatment_bundle["bundleArn"],
                            "bundleVersion": treatment_bundle["versionId"],
                        }
                    },
                },
            ],
            evaluationConfig={"onlineEvaluationConfigArn": settings.online_eval_arn},
            roleArn=settings.ab_test_role_arn,
            enableOnCreate=True,
            clientToken=str(uuid.uuid4()),
        )
        ab_test_id = created["abTestId"]
        print(f"A/B test: {ab_test_id} ({created.get('status')})")
        for _ in range(60):
            state = data.get_ab_test(abTestId=ab_test_id)
            if state.get("status") == "ACTIVE" and state.get("executionStatus") == "RUNNING":
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"A/B test {ab_test_id} did not become active")

        rows = _run_sessions(token, run_id)
        final = _poll_results(data, ab_test_id)
        payload = {
            "run_id": run_id,
            "ab_test_id": ab_test_id,
            "bundles": {
                "control": {
                    k: control_bundle.get(k) for k in ("bundleArn", "bundleId", "versionId")
                },
                "treatment": {
                    k: treatment_bundle.get(k) for k in ("bundleArn", "bundleId", "versionId")
                },
            },
            "prompts": PROMPTS,
            "sessions": rows,
            "results": final.get("results") if final else None,
            "status": final.get("status") if final else None,
            "execution_status": final.get("executionStatus") if final else None,
        }
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / "optimization.json").write_text(json.dumps(payload, indent=2, default=str))
        if final and final.get("results"):
            for metric in final["results"].get("evaluatorMetrics", []):
                ctrl = metric.get("controlStats", {})
                print(
                    f"{metric.get('evaluatorArn')}: control mean={ctrl.get('mean')} "
                    f"n={ctrl.get('sampleSize')}"
                )
                for variant in metric.get("variantResults", []):
                    print(
                        f"  {variant.get('variantName')}: mean={variant.get('mean')} "
                        f"n={variant.get('sampleSize')} p={variant.get('pValue')} "
                        f"significant={variant.get('isSignificant')}"
                    )
        else:
            print("no evaluator results before the deadline")
        return 0
    finally:
        if ab_test_id:
            _stop_and_delete(data, ab_test_id)
        _delete_bundles(control, bundles)


if __name__ == "__main__":
    raise SystemExit(main())
