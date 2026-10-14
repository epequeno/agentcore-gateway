"""CDK app entry point."""

import aws_cdk as cdk
from stacks.agentcore import AgentCoreStack

app = cdk.App()

# Region only — deliberately no account id, so nothing account-specific is
# committed to a repo that may be published.
AgentCoreStack(
    app,
    "AgentCoreGatewayStack",
    env=cdk.Environment(region=app.node.try_get_context("region") or "us-east-1"),
)

app.synth()
