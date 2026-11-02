"""CDK app entry point."""

import sys
from pathlib import Path

import aws_cdk as cdk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stacks.agentcore import AgentCoreStack  # noqa: E402

app = cdk.App()

# Region only — deliberately no account id, so nothing account-specific is
# committed to a repo that may be published.
AgentCoreStack(
    app,
    "AgentCoreGatewayStack",
    env=cdk.Environment(region=app.node.try_get_context("region") or "us-east-1"),
)

app.synth()
