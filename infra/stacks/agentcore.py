"""AgentCore stack — Runtime + Memory + Gateway.

The Gateway is the deliverable: it fronts the echo Lambda as an MCP tool, and
the agent reaches it only through the Gateway URL. Revoking the tool is a
Gateway-target operation, not a redeploy of the agent.

Every resource shape here was read out of the installed SDK
(``aws-cdk-lib`` ``aws_bedrockagentcore``) and the AWS docs, not inferred.

The L2 ``Runtime`` construct with ``AgentRuntimeArtifact.from_asset`` lets CDK
build the container image from the local Dockerfile and push it to a CDK-managed
ECR repository during ``cdk deploy`` — no manual build/push step.

The CDK deployment role needs ``iam:CreateServiceLinkedRole`` for the AgentCore
service-linked roles.
"""

import os

import aws_cdk as cdk
from aws_cdk import CfnOutput, Duration
from aws_cdk import aws_bedrockagentcore as bac
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

AGENT_RUNTIME_PRINCIPAL = "bedrock-agentcore.amazonaws.com"
MEMORY_EXPIRY_DAYS = 90

# The echo tool, deployed as a Lambda that the Gateway fronts as an MCP tool.
# Plain Python — no MCP code. The Gateway translates between MCP and Lambda.
# The handler returns the standard Lambda proxy shape; the Gateway parses
# ``body`` into an MCP CallToolResult.
ECHO_LAMBDA_CODE = """\
import json

def handler(event, context):
    text = event.get("text", "")
    return {"statusCode": 200, "body": json.dumps({"text": text})}
"""


class AgentCoreStack(cdk.Stack):
    """Runtime, Memory, Gateway, and the Lambda tool the Gateway fronts."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Runtime execution role ---------------------------------------
        runtime_role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal(AGENT_RUNTIME_PRINCIPAL),
            description="Execution role assumed by AgentCore Runtime",
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                # TODO: narrow to specific model ARNs once the model is fixed.
                resources=["*"],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                # AgentCore Memory data-plane operations for multi-turn continuity.
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:ListActors",
                    "bedrock-agentcore:ListSessions",
                ],
                resources=["*"],
            )
        )

        # --- Memory -------------------------------------------------------
        self.memory = bac.CfnMemory(
            self,
            "AgentMemory",
            name="AgentcoreGatewayMemory",
            event_expiry_duration=MEMORY_EXPIRY_DAYS,
            description="Cross-session memory for the gateway demonstration agent",
        )

        # --- Gateway ------------------------------------------------------
        # IAM inbound auth: the Runtime role signs requests with SigV4. No
        # client secret or token lives in the agent — the one credential is
        # the role it already runs as. This is the thesis of the repo.
        self.gateway = bac.Gateway(
            self,
            "AgentGateway",
            gateway_name="agentcore-gateway",
            description="Gateway fronting the echo Lambda as an MCP tool",
            authorizer_configuration=bac.GatewayAuthorizer.using_aws_iam(),
        )

        # --- Echo Lambda (the tool) ---------------------------------------
        echo_fn = lambda_.Function(
            self,
            "EchoTool",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(ECHO_LAMBDA_CODE),
            timeout=Duration.seconds(15),
            description="Echo tool published to the AgentCore Gateway",
        )

        # --- Gateway target: the Lambda behind the Gateway ---------------
        # Default outbound auth (IAM role) — the construct auto-grants
        # lambda:InvokeFunction to the gateway role. The agent discovers
        # this tool as "echo-target__echo" through the Gateway, never by
        # calling the Lambda directly.
        self.gateway.add_lambda_target(
            "EchoTarget",
            gateway_target_name="echo-target",
            description="The echo tool, reached only through the Gateway",
            lambda_function=echo_fn,
            tool_schema=bac.ToolSchema.from_inline(
                [
                    bac.ToolDefinition(
                        name="echo",
                        description="Return the text unchanged.",
                        input_schema=bac.SchemaDefinition(
                            type=bac.SchemaDefinitionType.OBJECT,
                            properties={
                                "text": bac.SchemaDefinition(
                                    type=bac.SchemaDefinitionType.STRING,
                                    description="The text to return.",
                                ),
                            },
                            required=["text"],
                        ),
                    ),
                ]
            ),
        )

        # Let the Runtime role invoke the Gateway (inbound IAM auth).
        self.gateway.grant_invoke(runtime_role)

        # --- Runtime (L2 with from_asset) --------------------------------
        # CDK builds the Dockerfile in the project root, pushes the image to
        # a CDK-managed ECR repository, and grants the runtime role pull
        # permissions — all during `cdk deploy`.
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.runtime = bac.Runtime(
            self,
            "AgentRuntime",
            runtime_name="agentcore_gateway_agent",
            description="Strands agent whose tools are supplied by AgentCore Gateway",
            agent_runtime_artifact=bac.AgentRuntimeArtifact.from_asset(
                project_root,
                exclude=[
                    ".venv",
                    "infra",
                    "tests",
                    "evals",
                    "docs",
                    ".git",
                    ".github",
                    ".pytest_cache",
                    ".ruff_cache",
                    "cdk.out",
                    "__pycache__",
                    "*.pyc",
                    "CACHEDIR.TAG",
                    "LOCAL.md",
                ],
            ),
            execution_role=runtime_role,
            environment_variables={
                # The Gateway URL is a CloudFormation GetAtt reference resolved
                # at deploy time. The agent appends /mcp to reach the MCP endpoint.
                "AGENTCORE_GATEWAY_URL": self.gateway.gateway_url,
                # The model the Strands agent calls via Bedrock. Override with
                # `cdk deploy -c model_id=...` or cdk.json context.
                "MODEL_ID": self.node.try_get_context("model_id") or "deepseek.v3.2",
                # Memory ID for multi-turn continuity via AgentCore Memory.
                "AGENTCORE_MEMORY_ID": self.memory.attr_memory_id,
            },
        )
        # The Runtime must exist after Memory.
        self.runtime.node.add_dependency(self.memory)

        CfnOutput(self, "RuntimeId", value=self.runtime.agent_runtime_id)
        CfnOutput(self, "RuntimeArn", value=self.runtime.agent_runtime_arn)
        CfnOutput(self, "MemoryId", value=self.memory.attr_memory_id)
        CfnOutput(self, "GatewayUrl", value=self.gateway.gateway_url)
        CfnOutput(self, "GatewayId", value=self.gateway.gateway_id)
