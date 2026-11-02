"""AgentCore stack — Runtime + Memory + Gateway + Policy + tools, Harness + A/B.

The Gateway is the deliverable: it fronts Lambda, MCP server, OpenAPI and Step
Functions targets, and the agent reaches every one only through the Gateway URL.
Code Interpreter and Browser are wrapped by a small MCP Runtime so they stay
inside the same boundary. Revoking a tool is a Gateway-target operation, not a
redeploy of the agent.

Every resource shape here was read out of the installed SDK
(``aws-cdk-lib`` ``aws_bedrockagentcore``) and the AWS docs, not inferred.

The L2 ``Runtime`` construct with ``AgentRuntimeArtifact.from_asset`` lets CDK
build the container image from the local Dockerfile and push it to a CDK-managed
ECR repository during ``cdk deploy`` — no manual build/push step.

The CDK deployment role needs ``iam:CreateServiceLinkedRole`` for the AgentCore
service-linked roles.
"""

import json
import os

import aws_cdk as cdk
from aws_cdk import CfnOutput, Duration
from aws_cdk import aws_agentregistry as agent_registry
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_authorizers
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_bedrockagentcore as bac
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import custom_resources as cr
from constructs import Construct

from agent.prompts import BASELINE_SYSTEM_PROMPT

AGENT_RUNTIME_PRINCIPAL = "bedrock-agentcore.amazonaws.com"
MEMORY_EXPIRY_DAYS = 90

# Natural-language policy prompt for the quota target. The generated Cedar is
# committed below; the prompt is kept so the provenance is inspectable.
NL_POLICY_PROMPT = (
    "Deny any request to the policy-quota___spend tool when the amount argument "
    "is greater than 100. The deny applies to every authenticated caller and "
    "only to this gateway."
)

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

# A deliberately small parameterised tool. The policy engine, not this Lambda,
# enforces the quota; the handler only reports the amount it was asked to spend.
QUOTA_LAMBDA_CODE = """\
import json

def handler(event, context):
    amount = event.get("amount", 0)
    return {"statusCode": 200, "body": json.dumps({"amount": amount, "status": "approved"})}
"""

# The downstream "CRM" the Gateway reaches on the user's behalf. The interceptor
# forwards the caller's Cognito token in a custom header; validating it with
# Cognito proves a user principal reaches the tool — not a shared service
# credential.
MCP_SERVER_CODE = """\
import json

import boto3


def _reply(request_id, result=None, error=None):
    body = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _principal(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    token = headers.get("x-agentcore-user-token", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise ValueError("missing user token")
    user = boto3.client("cognito-idp").get_user(AccessToken=token)
    attributes = {a["Name"]: a["Value"] for a in user.get("UserAttributes", [])}
    return attributes.get("sub") or user.get("Username"), attributes.get("email", "")


def handler(event, context):
    request = json.loads(event.get("body") or "{}")
    method = request.get("method")
    request_id = request.get("id")

    if method == "notifications/initialized":
        return {"statusCode": 202, "body": ""}

    if method == "initialize":
        return _reply(
            request_id,
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "crm-mcp", "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return _reply(
            request_id,
            {
                "tools": [
                    {
                        "name": "getAccount",
                        "description": "Return the signed-in user's CRM account.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            },
        )
    if method == "tools/call":
        params = request.get("params") or {}
        if params.get("name") != "getAccount":
            return _reply(request_id, error={"code": -32602, "message": "unknown tool"})
        try:
            principal, email = _principal(event)
        except Exception:  # noqa: BLE001 - any validation failure is a 401
            return _reply(request_id, error={"code": -32001, "message": "invalid user token"})
        return _reply(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"account": "ACME-42", "principal": principal, "email": email}
                        ),
                    }
                ],
                "isError": False,
            },
        )
    return _reply(request_id, error={"code": -32601, "message": "method not found"})
"""

# Forwards the caller's Authorization header to the MCP target. The Gateway has
# already validated that token; the target re-validates it with Cognito, so the
# tool sees the user principal without a portal or an outbound OAuth provider.
INTERCEPTOR_CODE = """\
def handler(event, context):
    request = (event.get("mcp") or {}).get("gatewayRequest") or {}
    headers = {k.lower(): v for k, v in (request.get("headers") or {}).items()}
    authorization = headers.get("authorization", "")
    request_id = (request.get("body") or {}).get("id")
    if not authorization:
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "statusCode": 401,
                    "body": {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32001, "message": "missing authorization"},
                    },
                }
            },
        }
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "headers": {"x-agentcore-user-token": authorization},
                "body": request.get("body") or {},
            }
        },
    }
"""

DISCOVERY_WAITER_CODE = """\
import time
import urllib.request


def handler(event, context):
    if event["RequestType"] == "Delete":
        return {"PhysicalResourceId": event["PhysicalResourceId"]}

    url = event["ResourceProperties"]["url"]
    last_error = None
    for _ in range(24):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return {"PhysicalResourceId": "discovery-ready"}
        except Exception as exc:  # noqa: BLE001 - report the last failure
            last_error = str(exc)
        time.sleep(5)
    raise RuntimeError(f"OIDC discovery never became reachable: {last_error}")
"""

OPENAPI_LAMBDA_CODE = """\
import hmac
import json
import os

import boto3


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    expected = json.loads(
        boto3.client("secretsmanager")
        .get_secret_value(SecretId=os.environ["OPENAPI_KEY_SECRET_ARN"])["SecretString"]
    )["apiKey"]
    presented = headers.get("x-api-key", "")
    if presented.lower().startswith("bearer "):
        presented = presented[7:].strip()
    # Token Vault stores the full generated secret string; accept either the
    # raw key or the JSON envelope it came from.
    if presented.startswith("{"):
        try:
            presented = json.loads(presented).get("apiKey", presented)
        except json.JSONDecodeError:
            pass
    if not hmac.compare_digest(presented, expected):
        return {
            "statusCode": 401,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"error": "invalid API key"}),
        }
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"status": "openapi-target-ok"}),
    }
"""

PRE_TOKEN_CODE = """\
def handler(event, context):
    client_id = event["callerContext"]["clientId"]
    attributes = event["request"].get("userAttributes", {})
    response = event.get("response") or {}
    details = response.get("claimsAndScopeOverrideDetails") or {}
    details["accessTokenGeneration"] = {
        "claimsToAddOrOverride": {
            "aud": client_id,
            "email": attributes.get("email", ""),
            "email_verified": attributes.get("email_verified") == "true",
        }
    }
    response["claimsAndScopeOverrideDetails"] = details
    event["response"] = response
    return event
"""

RUBRIC_INSTRUCTIONS = """\
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

# The tool half of the catalog record. Names are the Gateway's prefixed names,
# so a consumer that searches the registry gets the same identifiers the agent
# sees after `tools/list`.
GATEWAY_CATALOG_TOOLS = [
    {
        "name": "crm-mcp___getAccount",
        "description": "Return the signed-in user's CRM account and identity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "echo-target___echo",
        "description": "Echo text back unchanged; a minimal Lambda-backed tool.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo."}},
            "required": ["text"],
        },
    },
    {
        "name": "openapi-status___fetchStatus",
        "description": "Return the OpenAPI-backed service status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "policy-quota___spend",
        "description": "Request a spend amount; Gateway policy enforces the quota.",
        "inputSchema": {
            "type": "object",
            "properties": {"amount": {"type": "integer", "description": "Amount to spend."}},
            "required": ["amount"],
        },
    },
    {
        "name": "toolbox___browse_page",
        "description": "Fetch a web page through the managed Browser runtime.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Page URL."}},
            "required": ["url"],
        },
    },
    {
        "name": "toolbox___execute_python",
        "description": "Run Python in the managed Code Interpreter runtime.",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source."}},
            "required": ["code"],
        },
    },
    {
        "name": "workflow___StartExecution",
        "description": "Start the support workflow in the Step Functions target.",
        "inputSchema": {
            "type": "object",
            "properties": {"input": {"type": "string", "description": "Workflow input JSON."}},
            "required": ["input"],
        },
    },
]


class AgentCoreStack(cdk.Stack):
    """Runtime, Memory, Gateway, Policy, and the heterogeneous tool surface."""

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
        # ADOT exports spans and logs from the container. Unified telemetry puts
        # spans in the runtime's own log group, so the role needs PutLogEvents
        # there and PutResourcePolicy so X-Ray may deliver to it.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                    "logs:DescribeResourcePolicies",
                    "logs:PutResourcePolicy",
                ],
                resources=[
                    self.format_arn(
                        service="logs", resource="log-group:/aws/bedrock-agentcore/runtimes/*"
                    )
                ],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    self.format_arn(
                        service="logs",
                        resource="log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
                    )
                ],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                resources=["*"],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
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

        # --- Identity: Cognito as the primary IdP -------------------------
        # One user pool backs three clients: the portal (sign-in for consent),
        # the API client the eval uses to mint a user token, and the CRM client
        # the outbound 3LO credential provider represents. The pool's domain is
        # the OIDC issuer the Gateway trusts and the portal references.
        self.user_pool = cognito.UserPool(
            self,
            "SupportUsers",
            user_pool_name="agentcore-support-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True, username=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        self.cognito_domain = self.user_pool.add_domain(
            "SupportDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                # Account-scoped so the prefix is unique without a custom domain.
                domain_prefix=f"agentcore-identity-{cdk.Aws.ACCOUNT_ID}",
            ),
        )
        issuer = self.cognito_domain.base_url()
        # Cognito's discovery document lives on the cognito-idp endpoint, not
        # on the hosted-UI domain. The domain still serves authorize/token.
        discovery_url = (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{self.user_pool.user_pool_id}/.well-known/openid-configuration"
        )

        discovery_waiter_fn = lambda_.Function(
            self,
            "DiscoveryWaiterFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(DISCOVERY_WAITER_CODE),
            timeout=Duration.seconds(150),
        )
        discovery_ready = cdk.CustomResource(
            self,
            "DiscoveryReady",
            service_token=cr.Provider(
                self, "DiscoveryWaiterProvider", on_event_handler=discovery_waiter_fn
            ).service_token,
            properties={"url": discovery_url},
        )

        # Cognito access tokens omit `aud` and the email claims by default. The
        # V2 trigger adds them so the Gateway JWT authorizer and the MCP tool
        # both see the same identity.
        pre_token_fn = lambda_.Function(
            self,
            "PreTokenGenerationFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(PRE_TOKEN_CODE),
            timeout=Duration.seconds(10),
        )
        self.user_pool.add_trigger(
            cognito.UserPoolOperation.PRE_TOKEN_GENERATION_CONFIG,
            pre_token_fn,
            lambda_version=cognito.LambdaVersion.V2_0,
        )

        common_oauth = dict(
            flows=cognito.OAuthFlows(authorization_code_grant=True),
            scopes=[
                cognito.OAuthScope.OPENID,
                cognito.OAuthScope.EMAIL,
                cognito.OAuthScope.PROFILE,
            ],
            # Placeholder until scripts/identity_setup.sh registers the real
            # portal callback; the portal URL does not exist during synth.
            callback_urls=["http://localhost:3000/callback"],
        )
        self.api_client = self.user_pool.add_client(
            "ApiClient",
            user_pool_client_name="agentcore-api",
            generate_secret=False,
            # ADMIN_USER_PASSWORD_AUTH lets the eval mint the test user's token
            # without a hosted-UI browser round trip.
            auth_flows=cognito.AuthFlow(user_password=True, admin_user_password=True),
            o_auth=cognito.OAuthSettings(**common_oauth),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
        )
        interceptor_fn = lambda_.Function(
            self,
            "GatewayInterceptor",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(INTERCEPTOR_CODE),
            timeout=Duration.seconds(10),
            description="Forward the validated caller token to the MCP target",
        )

        # --- Policy engine (phase 3) --------------------------------------
        # Cedar/Dogwood policies are evaluated by the Gateway, outside the
        # agent's code. ENFORCE makes a deny a Gateway decision, not a prompt
        # instruction the model can ignore.
        self.policy_engine = bac.PolicyEngine(
            self,
            "GatewayPolicyEngine",
            policy_engine_name="gateway_policy",
            description="Deterministic limits for Gateway tools",
        )

        # --- Gateway ------------------------------------------------------
        # JWT inbound auth via Cognito. The caller's user token is validated at
        # the Gateway; the Runtime role is no longer a credential for tool calls.
        self.gateway = bac.Gateway(
            self,
            "AgentGateway",
            gateway_name="agentcore-gateway",
            description="Gateway fronting the echo, quota, and CRM tools",
            authorizer_configuration=bac.GatewayAuthorizer.using_custom_jwt(
                discovery_url=discovery_url,
                allowed_clients=[self.api_client.user_pool_client_id],
            ),
            interceptor_configurations=[
                bac.LambdaInterceptor.for_request(interceptor_fn, pass_request_headers=True)
            ],
            policy_engine_configuration=bac.GatewayPolicyEngineConfig(
                policy_engine=self.policy_engine,
                mode=bac.PolicyEngineMode.ENFORCE,
            ),
        )
        self.gateway.node.add_dependency(discovery_ready)
        # 3LO outbound auth is only accepted on the newer MCP revision. The L2
        # enum in this CDK version stops at 2025-06-18, so set the L1 property.
        cfn_gateway = self.gateway.node.default_child
        cfn_gateway.protocol_configuration = bac.CfnGateway.GatewayProtocolConfigurationProperty(
            mcp=bac.CfnGateway.MCPGatewayConfigurationProperty(supported_versions=["2025-11-25"])
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
        # this tool as "echo-target___echo" through the Gateway, never by
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

        # --- Quota Lambda (phase 3 policy parameter) -----------------------
        quota_fn = lambda_.Function(
            self,
            "QuotaTool",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(QUOTA_LAMBDA_CODE),
            timeout=Duration.seconds(15),
            description="Parameterised spend tool used by the policy engine",
        )
        self.gateway.add_lambda_target(
            "QuotaTarget",
            gateway_target_name="policy-quota",
            description="A spend tool whose amount is limited by Cedar policy",
            lambda_function=quota_fn,
            tool_schema=bac.ToolSchema.from_inline(
                [
                    bac.ToolDefinition(
                        name="spend",
                        description="Approve a spend of the given amount.",
                        input_schema=bac.SchemaDefinition(
                            type=bac.SchemaDefinitionType.OBJECT,
                            properties={
                                "amount": bac.SchemaDefinition(
                                    type=bac.SchemaDefinitionType.INTEGER,
                                    description="The amount to spend.",
                                ),
                            },
                            required=["amount"],
                        ),
                    ),
                ]
            ),
        )

        # --- CRM MCP server ------------------------------------------------
        # The Gateway's MCP-server target speaks JSON-RPC to this endpoint.
        # API Gateway accepts only the Gateway's IAM-signed request; the
        # interceptor sends the caller's Cognito token in a custom header.
        mcp_fn = lambda_.Function(
            self,
            "CrmMcpFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(MCP_SERVER_CODE),
            timeout=Duration.seconds(15),
            description="MCP server exposing the CRM account tool",
        )
        self.crm_mcp_api = apigwv2.HttpApi(
            self,
            "CrmMcpApi",
            api_name="agentcore-crm-mcp",
            description="MCP endpoint behind the Gateway target",
        )
        self.crm_mcp_api.add_routes(
            path="/mcp",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("CrmMcpIntegration", mcp_fn),
            authorizer=apigwv2_authorizers.HttpIamAuthorizer(),
        )
        mcp_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["cognito-idp:GetUser"], resources=["*"])
        )
        self.gateway.role.add_to_policy(
            iam.PolicyStatement(
                actions=["execute-api:Invoke"],
                resources=[self.crm_mcp_api.arn_for_execute_api("POST", "/mcp")],
            )
        )

        # The interceptor forwards the inbound user token in the allowlisted
        # header, so the downstream MCP server sees the Cognito principal.
        self.crm_target = bac.CfnGatewayTarget(
            self,
            "CrmMcpTarget",
            gateway_identifier=self.gateway.gateway_id,
            name="crm-mcp",
            description="CRM MCP server reached with the caller's JWT",
            target_configuration=bac.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=bac.CfnGatewayTarget.McpTargetConfigurationProperty(
                    mcp_server=bac.CfnGatewayTarget.McpServerTargetConfigurationProperty(
                        endpoint=f"{self.crm_mcp_api.url.rstrip('/')}/mcp"
                    )
                )
            ),
            metadata_configuration=bac.CfnGatewayTarget.MetadataConfigurationProperty(
                allowed_request_headers=["x-agentcore-user-token"]
            ),
            credential_provider_configurations=[
                bac.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                    credential_provider=bac.CfnGatewayTarget.CredentialProviderProperty(
                        iam_credential_provider=bac.CfnGatewayTarget.IamCredentialProviderProperty(
                            service="execute-api", region=self.region
                        )
                    ),
                )
            ],
        )

        # --- Policy enforcement permissions -------------------------------
        # The Gateway execution role evaluates policies. Without these grants,
        # attaching an engine makes every tool call deny by default.
        self.gateway.role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:GetPolicyEngine"],
                resources=[self.policy_engine.policy_engine_arn],
            )
        )
        self.gateway.role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:AuthorizeAction",
                    "bedrock-agentcore:PartiallyAuthorizeActions",
                ],
                resources=[
                    self.policy_engine.policy_engine_arn,
                    # The Gateway ARN is not available without making the role
                    # depend on the Gateway it is attached to. Scope by type and
                    # account instead; the engine ARN above is already exact.
                    f"arn:{cdk.Aws.PARTITION}:bedrock-agentcore:{self.region}:"
                    f"{cdk.Aws.ACCOUNT_ID}:gateway/*",
                ],
            )
        )
        # Temporal policies mint a workload access token so session history can
        # be bound to the caller. This is required even though target calls use
        # IAM, and the docs scope it to the Gateway's workload-identity directory.
        workload_identity = (
            f"arn:{cdk.Aws.PARTITION}:bedrock-agentcore:{self.region}:"
            f"{cdk.Aws.ACCOUNT_ID}:workload-identity-directory/default"
        )
        self.gateway.role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:GetWorkloadAccessToken"],
                resources=[
                    workload_identity,
                    f"{workload_identity}/workload-identity/*",
                ],
            )
        )

        # --- Baseline permit + temporal limit -----------------------------
        # Default-deny means even a list of tools needs a permit. This baseline
        # allows the catalog; the forbids below are the actual limits. The
        # quota rule was generated from natural language and is committed below.
        baseline = bac.Policy(
            self,
            "GatewayBaselinePermit",
            policy_engine=self.policy_engine,
            policy_name="gateway_baseline_permit",
            description="Allow the Gateway catalog; limits are separate forbids.",
            statement=bac.PolicyStatement.from_cedar(
                "permit(principal, action, resource == "
                f'AgentCore::Gateway::"{self.gateway.gateway_arn}");'
            ),
            # A broad permit is intentionally permissive: the limits are the
            # forbids. Cedar analysis flags this as "overly permissive", which
            # is true and not a reason to weaken the baseline.
            validation_mode=bac.PolicyValidationMode.IGNORE_ALL_FINDINGS,
        )
        baseline.node.add_dependency(self.gateway)

        # Dogwood (Cedar-compatible) temporal rule: at most one CRM lookup per
        # policy session. The count includes the request being authorized, so
        # the second call is denied. The agent supplies a session id header.
        temporal = bac.CfnPolicy(
            self,
            "CrmOncePerSession",
            policy_engine_id=self.policy_engine.policy_engine_id,
            name="crm_once_per_session",
            description="Deny a second CRM account lookup in the same policy session.",
            validation_mode="IGNORE_ALL_FINDINGS",
            enforcement_mode="ACTIVE",
            definition=bac.CfnPolicy.PolicyDefinitionProperty(
                policy=bac.CfnPolicy.PolicyStatementProperty(
                    statement=(
                        "forbid ("
                        "principal, "
                        'action == AgentCore::Action::"crm-mcp___getAccount", '
                        f'resource == AgentCore::Gateway::"{self.gateway.gateway_arn}"'
                        ") "
                        "when temporal { "
                        "exists (n: Long). "
                        "(count for (t: Timepoint). "
                        "where (formerly within 10m "
                        '(AgentCore::Action::"crm-mcp___getAccount"::request '
                        "{ eventResource: resource } && tp(t)))) == n "
                        "&& n > 1 "
                        "};"
                    )
                )
            ),
        )
        temporal.node.add_dependency(self.gateway)
        temporal.node.add_dependency(self.crm_target)

        # Natural-language-authored policy. StartPolicyGeneration on 2026-11-02
        # turned NL_POLICY_PROMPT into:
        #   forbid (...) when { context.input.amount > 100 };
        # The generated statement is committed verbatim except for the ARN,
        # which is templated so no account id enters the repo.
        quota_limit = bac.CfnPolicy(
            self,
            "QuotaLimit",
            policy_engine_id=self.policy_engine.policy_engine_id,
            name="quota_limit",
            description="Generated from natural language: deny spend amounts above 100.",
            validation_mode="FAIL_ON_ANY_FINDINGS",
            enforcement_mode="ACTIVE",
            definition=bac.CfnPolicy.PolicyDefinitionProperty(
                cedar=bac.CfnPolicy.CedarPolicyProperty(
                    statement=(
                        "forbid ("
                        "principal, "
                        'action == AgentCore::Action::"policy-quota___spend", '
                        f'resource == AgentCore::Gateway::"{self.gateway.gateway_arn}"'
                        ") when { context.input.amount > 100 };"
                    )
                )
            ),
        )
        quota_limit.node.add_dependency(self.gateway)
        # Validation must see the baseline permit, otherwise the service rejects
        # the generated forbid as overly restrictive before the permit exists.
        quota_limit.node.add_dependency(baseline)

        # The Runtime authenticates to the Gateway with the caller's user token,
        # not with its IAM role for tool calls.

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
                # AgentCore Runtime only accepts arm64 images. The stack builds
                # one with buildx; cross-builds need qemu binfmt registered.
                platform=ecr_assets.Platform.LINUX_ARM64,
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
                # --- Observability (phase 1) ------------------------------
                # The container's CMD is `opentelemetry-instrument`, so ADOT
                # installs the tracer provider and exports to CloudWatch.
                "AGENT_OBSERVABILITY_ENABLED": "true",
                "OTEL_PYTHON_DISTRO": "aws_distro",
                "OTEL_PYTHON_CONFIGURATOR": "aws_configurator",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_TRACES_EXPORTER": "otlp",
                # A/B bundle baggage belongs to the front-door request. Do not
                # inject it into outbound MCP calls: the tool Gateway would try
                # to resolve an A/B bundle for tool descriptions, and that
                # lookup is neither part of this experiment nor a valid
                # downstream context.
                "OTEL_PROPAGATORS": "tracecontext",
                # `{RuntimeName}.DEFAULT` is the service name batch evaluation
                # expects. The project attribute groups traces in Phoenix.
                "OTEL_RESOURCE_ATTRIBUTES": (
                    "service.name=agentcore_gateway_agent.DEFAULT,"
                    "openinference.project.name=agentcore-gateway"
                ),
                # Spans land in the agent's own log group, next to its logs, so
                # one log group is the session source for batch evaluation.
                "UNIFIED_TRACES_DESTINATION_ENABLED": "true",
            },
        )
        # The Runtime must exist after Memory.
        self.runtime.node.add_dependency(self.memory)

        # --- Phase 6: front door, online evaluation and managed harness ---
        # A/B tests route traffic through a Gateway target, not around it. This
        # is a second gateway so the tool gateway remains MCP-only and keeps its
        # JWT authorizer; the front door is an IAM-authenticated HTTP gateway
        # whose only target is the Runtime.
        self.front_gateway = bac.Gateway(
            self,
            "AgentFrontGateway",
            gateway_name="agentcore-agent-front",
            description="Runtime front door for AgentCore A/B routing",
            authorizer_configuration=bac.GatewayAuthorizer.using_aws_iam(),
        )
        # The front door carries HTTP Runtime traffic, not MCP tools. The L2
        # defaults a Gateway to MCP; clear both properties so the target type
        # is allowed to be an HTTP runtime.
        cfn_front = self.front_gateway.node.default_child
        cfn_front.protocol_type = None
        cfn_front.protocol_configuration = None
        self.front_target = bac.CfnGatewayTarget(
            self,
            "AgentFrontTarget",
            gateway_identifier=self.front_gateway.gateway_id,
            name="agent",
            description="The deployed Runtime, reached through the A/B front door",
            target_configuration=bac.CfnGatewayTarget.TargetConfigurationProperty(
                http=bac.CfnGatewayTarget.HttpTargetConfigurationProperty(
                    agentcore_runtime=bac.CfnGatewayTarget.RuntimeTargetConfigurationProperty(
                        arn=self.runtime.agent_runtime_arn,
                        qualifier="DEFAULT",
                    )
                )
            ),
        )
        self.runtime.grant_invoke_runtime(self.front_gateway.role)

        # A Runtime that reads a configuration bundle calls the bundle API with
        # its execution role; the Gateway roles can also resolve bundles while
        # routing. Grant all three the read actions explicitly.
        for bundle_reader in (runtime_role, self.gateway.role, self.front_gateway.role):
            bundle_reader.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "bedrock-agentcore:GetConfigurationBundle",
                        "bedrock-agentcore:GetConfigurationBundleVersion",
                        "bedrock-agentcore:ListConfigurationBundleVersions",
                    ],
                    resources=["*"],
                )
            )

        # Online evaluation is the reward signal for the A/B test. It samples
        # the same Runtime's spans and writes service-managed results.
        self.online_eval = bac.OnlineEvaluationConfig(
            self,
            "PromptOnlineEvaluation",
            online_evaluation_config_name="agentcore_prompt_eval",
            description="Correctness and goal success over Runtime sessions",
            data_source=bac.DataSourceConfig.from_agent_runtime_endpoint(self.runtime),
            evaluators=[
                bac.EvaluatorSelector.builtin(bac.BuiltinEvaluator.CORRECTNESS),
                bac.EvaluatorSelector.builtin(bac.BuiltinEvaluator.GOAL_SUCCESS_RATE),
            ],
            sampling_percentage=100,
            session_timeout=Duration.minutes(2),
            execution_status=bac.ExecutionStatus.ENABLED,
        )
        # The A/B service assumes this role to read the two bundle versions and
        # the online-evaluation config while it routes sessions.
        self.ab_role = iam.Role(
            self,
            "ABTestExecutionRole",
            assumed_by=iam.ServicePrincipal(AGENT_RUNTIME_PRINCIPAL),
            description="Execution role for the prompt A/B test",
        )
        self.ab_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:GetGatewayTarget",
                    "bedrock-agentcore:ListGatewayTargets",
                    "bedrock-agentcore:CreateGatewayRule",
                    "bedrock-agentcore:UpdateGatewayRule",
                    "bedrock-agentcore:GetGatewayRule",
                    "bedrock-agentcore:DeleteGatewayRule",
                    "bedrock-agentcore:ListGatewayRules",
                    "bedrock-agentcore:GetOnlineEvaluationConfig",
                    "bedrock-agentcore:GetEvaluator",
                    "bedrock-agentcore:GetConfigurationBundle",
                    "bedrock-agentcore:GetConfigurationBundleVersion",
                    "bedrock-agentcore:ListConfigurationBundleVersions",
                ],
                resources=["*"],
            )
        )
        self.ab_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:DescribeLogGroups"],
                resources=["*"],
            )
        )
        self.ab_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:DescribeIndexPolicies",
                    "logs:PutIndexPolicy",
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "logs:StopQuery",
                    "logs:FilterLogEvents",
                    "logs:GetLogEvents",
                ],
                resources=[
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/evaluations/*",
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtimes/*",
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtime/*",
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/harness/*",
                    "arn:aws:logs:*:*:log-group:aws/spans",
                    "arn:aws:logs:*:*:log-group:aws/spans:*",
                ],
            )
        )

        # Managed Harness: same model and system prompt as the Runtime, with
        # tools supplied per invocation so the measurement can pass a fresh
        # Cognito token instead of baking one into a resource.
        harness_role = iam.Role(
            self,
            "HarnessExecutionRole",
            assumed_by=iam.ServicePrincipal(AGENT_RUNTIME_PRINCIPAL),
            description="Execution role for the AgentCore Harness comparison",
        )
        harness_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                    "bedrock:GetFoundationModel",
                    "bedrock:ListFoundationModels",
                ],
                resources=["*"],
            )
        )
        harness_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=["*"],
            )
        )
        self.harness = bac.CfnHarness(
            self,
            "ComparisonHarness",
            harness_name="agentcore_runtime_harness",
            execution_role_arn=harness_role.role_arn,
            model=bac.CfnHarness.HarnessModelConfigurationProperty(
                bedrock_model_config=bac.CfnHarness.HarnessBedrockModelConfigProperty(
                    model_id=self.node.try_get_context("model_id") or "deepseek.v3.2",
                    temperature=0.0,
                    max_tokens=1024,
                )
            ),
            system_prompt=[
                bac.CfnHarness.HarnessSystemContentBlockProperty(text=BASELINE_SYSTEM_PROMPT)
            ],
            allowed_tools=["*"],
            max_iterations=6,
            max_tokens=2048,
            timeout_seconds=120,
            memory=bac.CfnHarness.HarnessMemoryConfigurationProperty(disabled={}),
            truncation=bac.CfnHarness.HarnessTruncationConfigurationProperty(
                strategy="sliding_window",
                config=bac.CfnHarness.HarnessTruncationStrategyConfigurationProperty(
                    sliding_window=bac.CfnHarness.HarnessSlidingWindowConfigurationProperty(
                        messages_count=20
                    )
                ),
            ),
            environment=bac.CfnHarness.HarnessEnvironmentProviderProperty(
                agent_core_runtime_environment=bac.CfnHarness.HarnessAgentCoreRuntimeEnvironmentProperty(
                    network_configuration=bac.CfnHarness.NetworkConfigurationProperty(
                        network_mode="PUBLIC"
                    )
                )
            ),
        )

        # --- Phase 4: heterogeneous targets ------------------------------
        # OpenAPI target: a Function URL whose Lambda enforces the API key the
        # Gateway presents. The key is generated by CloudFormation and also
        # stored in AgentCore Token Vault, so no literal lands in the template.
        openapi_key = secretsmanager.Secret(
            self,
            "OpenApiKeySecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template="{}",
                generate_string_key="apiKey",
                password_length=40,
            ),
        )
        openapi_key_provider = bac.ApiKeyCredentialProvider(
            self,
            "OpenApiKeyProvider",
            api_key_credential_provider_name="openapi-status-key",
            api_key=openapi_key.secret_value,
        )
        openapi_fn = lambda_.Function(
            self,
            "OpenApiStatusFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(OPENAPI_LAMBDA_CODE),
            timeout=Duration.seconds(10),
            environment={"OPENAPI_KEY_SECRET_ARN": openapi_key.secret_arn},
            description="OpenAPI target endpoint; validates the Gateway's API key",
        )
        openapi_key.grant_read(openapi_fn)
        openapi_url = openapi_fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE).url
        openapi_schema = {
            "openapi": "3.0.0",
            "info": {"title": "Gateway status", "version": "1.0.0"},
            "servers": [{"url": openapi_url}],
            "paths": {
                "/": {
                    "get": {
                        "operationId": "fetchStatus",
                        "summary": "Return the OpenAPI target's status.",
                        "responses": {"200": {"description": "Status payload"}},
                    }
                }
            },
        }
        self.openapi_target = self.gateway.add_open_api_target(
            "OpenApiStatusTarget",
            gateway_target_name="openapi-status",
            description="OpenAPI target with API-key outbound auth",
            api_schema=bac.ApiSchema.from_inline(json.dumps(openapi_schema)),
            credential_provider_configurations=[
                bac.GatewayCredentialProvider.from_api_key_identity(
                    openapi_key_provider,
                    credential_location=bac.ApiKeyCredentialLocation.header(
                        credential_parameter_name="x-api-key"
                    ),
                )
            ],
        )

        # Step Functions connector target: a Standard state machine pinned to
        # StartExecution. The agent passes only the workflow input; the target
        # removes stateMachineArn from the tool schema and enforces it.
        workflow_role = iam.Role(
            self,
            "WorkflowExecutionRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
            description="Execution role for the Gateway's Step Functions target",
        )
        workflow = sfn.StateMachine(
            self,
            "GatewayWorkflow",
            state_machine_name="agentcore-gateway-workflow",
            definition=sfn.Pass(
                self,
                "WorkflowResult",
                result=sfn.Result(value="step-functions-target-ok"),
            ),
            role=workflow_role,
        )
        self.gateway.role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:StartExecution"],
                resources=[workflow.state_machine_arn],
            )
        )
        self.workflow = workflow
        self.workflow_target = bac.CfnGatewayTarget(
            self,
            "WorkflowTarget",
            gateway_identifier=self.gateway.gateway_id,
            name="workflow",
            description="Step Functions connector target; one pinned Standard workflow",
            target_configuration=bac.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=bac.CfnGatewayTarget.McpTargetConfigurationProperty(
                    connector=bac.CfnGatewayTarget.ConnectorTargetConfigurationProperty(
                        source=bac.CfnGatewayTarget.ConnectorSourceProperty(
                            connector_id="states",
                        ),
                        configurations=[
                            bac.CfnGatewayTarget.ConnectorConfigurationProperty(
                                name="StartExecution",
                                parameter_values={
                                    "stateMachineArn": workflow.state_machine_arn,
                                },
                            )
                        ],
                    )
                )
            ),
            credential_provider_configurations=[
                bac.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE"
                )
            ],
        )
        self.workflow_target.node.add_dependency(workflow)

        # MCP toolbox target: Code Interpreter and Browser remain AgentCore
        # managed tools, but the agent sees them only as Gateway tools. The
        # Runtime is private glue: narrow MCP operations, no direct agent path.
        toolbox_role = iam.Role(
            self,
            "ToolboxRuntimeRole",
            assumed_by=iam.ServicePrincipal(AGENT_RUNTIME_PRINCIPAL),
            description="Execution role for the Code Interpreter / Browser toolbox",
        )
        toolbox_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:*"],
                resources=[
                    # The built-in tools live in the AWS-owned account.
                    f"arn:{cdk.Aws.PARTITION}:bedrock-agentcore:{self.region}:aws:browser/*",
                    f"arn:{cdk.Aws.PARTITION}:bedrock-agentcore:{self.region}:aws:code-interpreter/*",
                    self.format_arn(
                        service="bedrock-agentcore",
                        resource="browser/*",
                    ),
                    self.format_arn(
                        service="bedrock-agentcore",
                        resource="code-interpreter/*",
                    ),
                ],
            )
        )
        for actions, resource in (
            (
                [
                    "logs:CreateLogGroup",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                    "logs:DescribeResourcePolicies",
                    "logs:PutResourcePolicy",
                ],
                "log-group:/aws/bedrock-agentcore/runtimes/*",
            ),
            (
                ["logs:CreateLogStream", "logs:PutLogEvents"],
                "log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
            ),
        ):
            toolbox_role.add_to_policy(
                iam.PolicyStatement(
                    actions=actions,
                    resources=[self.format_arn(service="logs", resource=resource)],
                )
            )
        self.toolbox = bac.Runtime(
            self,
            "ToolboxRuntime",
            runtime_name="agentcore_toolbox",
            description="MCP server exposing Code Interpreter and Browser through Gateway",
            agent_runtime_artifact=bac.AgentRuntimeArtifact.from_asset(
                os.path.join(project_root, "toolbox"),
                platform=ecr_assets.Platform.LINUX_ARM64,
                exclude=[
                    "__pycache__",
                    "*.pyc",
                    ".pytest_cache",
                    "LOCAL.md",
                ],
            ),
            execution_role=toolbox_role,
            authorizer_configuration=bac.RuntimeAuthorizerConfiguration.using_iam(),
            environment_variables={"AWS_REGION": self.region},
        )
        toolbox_cfn = self.toolbox.node.default_child
        toolbox_cfn.protocol_configuration = "MCP"
        self.toolbox.grant_invoke_runtime(self.gateway.role)
        encoded_toolbox_arn = cdk.Fn.join(
            "%2F",
            cdk.Fn.split(
                "/",
                cdk.Fn.join("%3A", cdk.Fn.split(":", self.toolbox.agent_runtime_arn)),
            ),
        )
        toolbox_endpoint = cdk.Fn.join(
            "",
            [
                f"https://bedrock-agentcore.{self.region}.amazonaws.com/runtimes/",
                encoded_toolbox_arn,
                "/invocations?qualifier=DEFAULT",
            ],
        )
        self.toolbox_target = self.gateway.add_mcp_server_target(
            "ToolboxTarget",
            gateway_target_name="toolbox",
            description="Code Interpreter and Browser exposed as Gateway tools",
            endpoint=toolbox_endpoint,
            credential_provider_configurations=[
                bac.GatewayCredentialProvider.from_iam_role(service="bedrock-agentcore")
            ],
        )
        self.toolbox_target.node.add_dependency(self.toolbox)

        # --- Registry (phase 5) ------------------------------------------
        # The registry is IAM-authorized and auto-approves records on
        # submission, so the same deploy publishes a discoverable catalog.
        model_id = self.node.try_get_context("model_id") or "deepseek.v3.2"
        self.registry = agent_registry.CfnRegistry(
            self,
            "AgentRegistry",
            name="agentcore-gateway-catalog",
            description=(
                "Support-operations agents and tools published by the "
                "AgentCore Gateway reference deployment."
            ),
            authorizer_type="AWS_IAM",
            approval_configuration=agent_registry.CfnRegistry.ApprovalConfigurationProperty(
                auto_approval_rules=["APPROVE_ALL"],
            ),
        )

        # The Runtime is not an A2A server, so its catalog descriptor is the
        # custom shape the registry supports for non-A2A agents. It carries the
        # deployed Runtime ARN, which is the fact the acceptance check compares.
        agent_descriptor = json.dumps(
            {
                "name": "agentcore-gateway-agent",
                "description": (
                    "Support operations agent built with Strands on AgentCore "
                    "Runtime. It answers customer account and billing questions "
                    "by discovering and calling CRM, policy, workflow, "
                    "code-execution and web-browsing tools through an AgentCore "
                    "Gateway."
                ),
                "version": "1.0.0",
                "protocol": "agentcore-runtime",
                "runtimeArn": self.runtime.agent_runtime_arn,
                "gatewayId": self.gateway.gateway_id,
                "gatewayUrl": self.gateway.gateway_url,
                "modelId": model_id,
                "tools": [tool["name"] for tool in GATEWAY_CATALOG_TOOLS],
            },
            separators=(",", ":"),
        )
        self.agent_record = agent_registry.CfnRegistryRecord(
            self,
            "AgentCatalogRecord",
            registry_id=self.registry.attr_registry_arn,
            name="agentcore-gateway-agent",
            display_name="AgentCore Gateway support agent",
            description=(
                "Support operations agent that answers customer questions by "
                "calling CRM, policy, workflow, code and browser tools through "
                "AgentCore Gateway."
            ),
            record_type="AGENT",
            record_version="1.0.0",
            descriptors=agent_registry.CfnRegistryRecord.DescriptorsProperty(
                custom=agent_registry.CfnRegistryRecord.CustomDescriptorProperty(
                    data=agent_descriptor,
                ),
            ),
        )

        # The MCP record describes the Gateway itself. Its tools are the same
        # prefixed names a fresh MCP client sees from Gateway `tools/list`.
        gateway_server = json.dumps(
            {
                "name": "io.agentcore/agentcore-gateway",
                "description": (
                    "Support-operations tools: CRM, quota policy, workflows, "
                    "code execution and browser access."
                ),
                "version": "1.0.0",
            },
            separators=(",", ":"),
        )
        gateway_tools = json.dumps(
            {"tools": GATEWAY_CATALOG_TOOLS},
            separators=(",", ":"),
        )
        self.tools_record = agent_registry.CfnRegistryRecord(
            self,
            "GatewayCatalogRecord",
            registry_id=self.registry.attr_registry_arn,
            name="agentcore-gateway-tools",
            display_name="AgentCore Gateway tools",
            description=(
                "Seven support-operations tools exposed by AgentCore Gateway "
                "across Lambda, MCP, OpenAPI and Step Functions targets."
            ),
            record_type="MCP",
            record_version="1.0.0",
            descriptors=agent_registry.CfnRegistryRecord.DescriptorsProperty(
                mcp_server=agent_registry.CfnRegistryRecord.McpServerDescriptorProperty(
                    data=gateway_server,
                    data_schema_version="2025-12-11",
                    additional_data=agent_registry.CfnRegistryRecord.McpServerAdditionalDataProperty(
                        tools=agent_registry.CfnRegistryRecord.McpToolsDescriptorProperty(
                            data=gateway_tools,
                            data_schema_version="2024-11-05",
                        )
                    ),
                )
            ),
        )

        # CfnRegistryRecord stops at DRAFT. `just registry-publish` submits
        # both records; APPROVE_ALL then makes them visible to search. The
        # discovery check stays data-plane-only, so it cannot approve itself.

        # --- Code-based evaluator (phase 1) ------------------------------
        # Deterministic assertion check over session spans. It is the control
        # that proves the evaluation pipeline can fail: scenario s11 carries a
        # seeded violation and must score FAIL every run.
        evaluator_fn = lambda_.Function(
            self,
            "EchoFidelityEvaluatorFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.echo_fidelity",
            code=lambda_.Code.from_asset(os.path.join(project_root, "evals", "evaluator")),
            timeout=Duration.seconds(30),
            description="Deterministic assertion evaluator for AgentCore Evaluations",
        )
        self.evaluator = bac.Evaluator(
            self,
            "EchoFidelityEvaluator",
            evaluator_name="EchoFidelity",
            level=bac.EvaluationLevel.SESSION,
            description="Checks the final response against seeded substring assertions.",
            evaluator_config=bac.EvaluatorConfig.code_based(
                lambda_function=evaluator_fn,
                timeout=Duration.seconds(30),
            ),
        )

        # The same judge model and rubric text are used by evals/phoenix.py, so
        # the pair isolates the platform from the prompt.
        judge_model = self.node.try_get_context("judge_model_id") or "deepseek.v3.2"
        self.rubric_evaluator = bac.Evaluator(
            self,
            "GatewayRubricEvaluator",
            evaluator_name="GatewayRubric",
            level=bac.EvaluationLevel.TRACE,
            description="Controlled rubric shared with the Phoenix comparison.",
            evaluator_config=bac.EvaluatorConfig.llm_as_a_judge(
                instructions=RUBRIC_INSTRUCTIONS,
                model_id=judge_model,
                rating_scale=bac.EvaluatorRatingScale.categorical(
                    [
                        {"label": "PASS", "definition": "The turn answers the user correctly."},
                        {"label": "FAIL", "definition": "The turn is wrong or invents a result."},
                    ]
                ),
                inference_config=bac.EvaluatorInferenceConfig(
                    max_tokens=512,
                    temperature=0.0,
                ),
            ),
        )

        CfnOutput(self, "RuntimeId", value=self.runtime.agent_runtime_id)
        CfnOutput(self, "RuntimeArn", value=self.runtime.agent_runtime_arn)
        CfnOutput(self, "MemoryId", value=self.memory.attr_memory_id)
        CfnOutput(self, "GatewayUrl", value=self.gateway.gateway_url)
        CfnOutput(self, "GatewayId", value=self.gateway.gateway_id)
        CfnOutput(self, "PolicyEngineArn", value=self.policy_engine.policy_engine_arn)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "ApiClientId", value=self.api_client.user_pool_client_id)
        CfnOutput(self, "CognitoIssuer", value=issuer)
        CfnOutput(self, "CrmTargetId", value=self.crm_target.attr_target_id)
        CfnOutput(self, "CrmMcpUrl", value=f"{self.crm_mcp_api.url.rstrip('/')}/mcp")
        CfnOutput(self, "EvaluatorArn", value=self.evaluator.evaluator_arn)
        CfnOutput(self, "RubricEvaluatorArn", value=self.rubric_evaluator.evaluator_arn)
        CfnOutput(self, "RegistryArn", value=self.registry.ref)
        CfnOutput(self, "RegistryId", value=self.registry.attr_registry_id)
        CfnOutput(self, "AgentRecordArn", value=self.agent_record.ref)
        CfnOutput(self, "ToolsRecordArn", value=self.tools_record.ref)
        CfnOutput(self, "ABTestRoleArn", value=self.ab_role.role_arn)
        CfnOutput(self, "HarnessArn", value=self.harness.attr_arn)
        CfnOutput(self, "HarnessId", value=self.harness.attr_harness_id)
        CfnOutput(self, "FrontGatewayId", value=self.front_gateway.gateway_id)
        CfnOutput(self, "FrontGatewayArn", value=self.front_gateway.gateway_arn)
        CfnOutput(self, "FrontGatewayUrl", value=self.front_gateway.gateway_url)
        CfnOutput(
            self,
            "OnlineEvaluationConfigArn",
            value=self.online_eval.online_evaluation_config_arn,
        )
        CfnOutput(
            self,
            "OnlineEvaluationConfigId",
            value=self.online_eval.online_evaluation_config_id,
        )
