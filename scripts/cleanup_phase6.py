"""Delete A/B tests and configuration bundles that live outside CloudFormation.

The phase 6 experiment creates these service resources at runtime. `just down`
calls this before destroying the stack so a crashed experiment cannot leave an
A/B test routing traffic or billable evaluator work behind.
"""

import sys
import time
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import settings  # noqa: E402

AB_PREFIX = "agentcore_prompt_ab_"
BUNDLE_PREFIXES = ("agentcore_prompt_control_", "agentcore_prompt_treatment_")


def main() -> None:
    region = settings.region
    data = boto3.client("bedrock-agentcore", region_name=region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    tests = data.list_ab_tests().get("abTests", [])
    for test in tests:
        if not test.get("name", "").startswith(AB_PREFIX):
            continue
        ab_id = test["abTestId"]
        try:
            data.update_ab_test(abTestId=ab_id, executionStatus="STOPPED")
        except Exception as exc:  # noqa: BLE001 - already stopped is fine
            print(f"{ab_id}: stop: {exc}")
        for _ in range(12):
            try:
                data.delete_ab_test(abTestId=ab_id)
                print(f"deleted A/B test {ab_id}")
                break
            except Exception as exc:  # noqa: BLE001 - state can still be UPDATING
                last_error = exc
                time.sleep(5)
        else:
            print(f"{ab_id}: delete: {last_error}")

    bundles = control.list_configuration_bundles().get("bundles", [])
    for bundle in bundles:
        if not bundle.get("bundleName", "").startswith(BUNDLE_PREFIXES):
            continue
        bundle_id = bundle["bundleId"]
        try:
            control.delete_configuration_bundle(bundleId=bundle_id)
            print(f"deleted bundle {bundle_id}")
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{bundle_id}: delete: {exc}")


if __name__ == "__main__":
    main()
