"""Audit that the application stack and its non-CloudFormation resources are gone.

`cdk destroy` removes the stack. It does not remove CloudWatch log groups,
A/B tests, configuration bundles, Registry records created outside the stack, or
built-in Browser/Code Interpreter sessions. This check fails if any of those
remain; `just down` runs the cleanup scripts first.

The shared CDK bootstrap stack, S3 asset buckets and ECR asset repository are
account-level infrastructure, not application resources. They are reported but
not treated as a failure; deleting them would break other CDK applications.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import settings  # noqa: E402

STACK_NAME = "AgentCoreGatewayStack"
LOG_PREFIXES = (
    "/aws/lambda/AgentCoreGatewayStack-",
    "/aws/bedrock-agentcore/runtimes/agentcore_",
    "/aws/bedrock-agentcore/runtimes/harness_agentcore_",
    "/aws/bedrock-agentcore/evaluations/results/agentcore_",
)


def _list(service: Any, operation: str, key: str) -> list[dict[str, Any]]:
    return getattr(service, operation)().get(key, [])


def _stack_exists(cloudformation: Any) -> bool:
    try:
        cloudformation.describe_stacks(StackName=STACK_NAME)
        return True
    except ClientError as exc:
        if "does not exist" in str(exc):
            return False
        raise


def _log_group_count(logs: Any) -> int:
    count = 0
    for page in logs.get_paginator("describe_log_groups").paginate():
        count += sum(
            1
            for group in page.get("logGroups", [])
            if group["logGroupName"].startswith(LOG_PREFIXES)
        )
    return count


def _active_sessions(agentcore: Any) -> tuple[int, int]:
    total = 0
    active = 0
    for identifier, operation in (
        ("aws.browser.v1", "list_browser_sessions"),
        ("aws.codeinterpreter.v1", "list_code_interpreter_sessions"),
    ):
        if operation == "list_browser_sessions":
            response = agentcore.list_browser_sessions(browserIdentifier=identifier)
        else:
            response = agentcore.list_code_interpreter_sessions(
                codeInterpreterIdentifier=identifier
            )
        items = response.get("sessions", response.get("items", []))
        total += len(items)
        active += sum(1 for item in items if item.get("status") != "TERMINATED")
    return total, active


def _bootstrap_residue(ecr: Any, s3: Any) -> tuple[int, int, int]:
    repositories = ecr.describe_repositories().get("repositories", [])
    assets = [
        repo
        for repo in repositories
        if repo["repositoryName"].startswith("cdk-hnb659fds-container-assets-")
    ]
    images = sum(
        len(ecr.list_images(repositoryName=repo["repositoryName"]).get("imageIds", []))
        for repo in assets
    )
    buckets = [
        bucket["Name"]
        for bucket in s3.list_buckets().get("Buckets", [])
        if bucket["Name"].startswith("cdk-hnb659fds-assets-")
    ]
    return len(assets), images, len(buckets)


def main() -> int:
    region = settings.region
    agentcore = boto3.client("bedrock-agentcore", region_name=region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    registry = boto3.client("agent-registry-control", region_name=region)
    logs = boto3.client("logs", region_name=region)
    cloudformation = boto3.client("cloudformation", region_name=region)
    ecr = boto3.client("ecr", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    checks: list[tuple[str, Callable[[], int]]] = [
        ("stack", lambda: int(_stack_exists(cloudformation))),
        ("agent runtimes", lambda: len(_list(control, "list_agent_runtimes", "agentRuntimes"))),
        ("gateways", lambda: len(_list(control, "list_gateways", "gateways"))),
        ("memories", lambda: len(_list(control, "list_memories", "memories"))),
        ("policy engines", lambda: len(_list(control, "list_policy_engines", "policyEngines"))),
        ("harnesses", lambda: len(_list(control, "list_harnesses", "harnesses"))),
        (
            "online eval configs",
            lambda: len(
                _list(control, "list_online_evaluation_configs", "onlineEvaluationConfigs")
            ),
        ),
        (
            "configuration bundles",
            lambda: len(_list(control, "list_configuration_bundles", "bundles")),
        ),
        ("A/B tests", lambda: len(_list(agentcore, "list_ab_tests", "abTests"))),
        ("registries", lambda: len(_list(registry, "list_registries", "registries"))),
        ("app log groups", lambda: _log_group_count(logs)),
    ]

    failures: list[str] = []
    print(f"region: {region}")
    for name, check in checks:
        try:
            value = check()
        except Exception as exc:  # noqa: BLE001 - a failed check is not a pass
            failures.append(name)
            print(f"{name:<24} CHECK FAILED: {exc}")
            continue
        print(f"{name:<24} {value}")
        if value:
            failures.append(name)

    total_sessions, active_sessions = _active_sessions(agentcore)
    print(f"{'built-in sessions':<24} {total_sessions} total, {active_sessions} active")
    if active_sessions:
        failures.append("active built-in sessions")

    try:
        asset_repos, asset_images, asset_buckets = _bootstrap_residue(ecr, s3)
        print(
            "CDK bootstrap retained: "
            f"{asset_repos} ECR repo(s), {asset_images} image tag(s), "
            f"{asset_buckets} S3 asset bucket(s)"
        )
    except Exception as exc:  # noqa: BLE001 - informational only
        print(f"CDK bootstrap: not checked ({exc})")

    if failures:
        print("FAIL: application resources remain: " + ", ".join(failures))
        return 1
    print("PASS: no application resources or app log groups remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
