"""AgentCore stack — Runtime + Memory.

The Gateway is the deliverable and is NOT in this stack yet; see the TODO block
and docs/API-NOTES.md.

Every resource shape here was mirrored from a working deployment rather than
inferred, and the required properties are recorded so nobody has to rediscover
them:

  AWS::BedrockAgentCore::Memory
      required: Name (^[a-zA-Z][a-zA-Z0-9_]{0,47}$), EventExpiryDuration (3-365 days)
  AWS::BedrockAgentCore::Runtime
      required: AgentRuntimeName, AgentRuntimeArtifact, NetworkConfiguration, RoleArn

The Runtime execution role must be assumable by ``bedrock-agentcore.amazonaws.com``
and NOT ``bedrock.amazonaws.com``. With the wrong principal the Runtime never
starts, and it surfaces as a deployment that hangs rather than an error that
explains itself.

Deploy order: the container image must exist in ECR before the Runtime resource is
created, so push the image first.
"""

import aws_cdk as cdk
from aws_cdk import CfnOutput, RemovalPolicy
from aws_cdk import aws_bedrockagentcore as bac
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct

AGENT_RUNTIME_PRINCIPAL = "bedrock-agentcore.amazonaws.com"
IMAGE_TAG = "0.1.0"
MEMORY_EXPIRY_DAYS = 90


class AgentCoreStack(cdk.Stack):
    """Runtime, its execution role, the image repository, and Memory."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

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

        repo = ecr.Repository(
            self,
            "AgentRepo",
            repository_name="agentcore-gateway-agent",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )
        repo.grant_pull(runtime_role)

        self.memory = bac.CfnMemory(
            self,
            "AgentMemory",
            name="AgentcoreGatewayMemory",
            event_expiry_duration=MEMORY_EXPIRY_DAYS,
            description="Cross-session memory for the gateway demonstration agent",
        )

        self.runtime = bac.CfnRuntime(
            self,
            "AgentRuntime",
            agent_runtime_name="agentcore_gateway_agent",
            description="Strands agent whose tools are supplied by AgentCore Gateway",
            agent_runtime_artifact=bac.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bac.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=repo.repository_uri_for_tag(IMAGE_TAG),
                ),
            ),
            network_configuration=bac.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC",
            ),
            role_arn=runtime_role.role_arn,
        )
        # addDependency is deprecated in aws-cdk-lib 2.x in favour of this form.
        self.runtime.add_resource_dependency(self.memory)

        # TODO(gateway): add bac.CfnGateway and bac.CfnGatewayTarget here, then
        # pass the Gateway URL to the agent as AGENTCORE_GATEWAY_URL. The verified
        # attribute is CfnGateway.attr_gateway_url. The agent must reach its tools
        # only through it — a direct target call defeats the point of the repo.

        CfnOutput(self, "RuntimeId", value=self.runtime.attr_agent_runtime_id)
        CfnOutput(self, "RuntimeArn", value=self.runtime.attr_agent_runtime_arn)
        CfnOutput(self, "MemoryId", value=self.memory.attr_memory_id)
        CfnOutput(self, "RepositoryUri", value=repo.repository_uri)
