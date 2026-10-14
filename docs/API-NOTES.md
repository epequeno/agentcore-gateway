# API notes — what is verified, and what is still an assumption

**Status: mostly verified.** The first table was read out of the installed SDKs
and the AWS docs. The second table holds the few facts that can only be resolved
at deploy time or against the pricing page.

This file exists because the last project in this portfolio was scaffolded from a
release-candidate blog post, and verifying it against the real specification later
found **ten-plus divergences** — including `initialize` having been removed
entirely. Assumptions about cloud APIs are expensive. Record them, then check them.

## Verified locally

Read from `bedrock_agentcore` 1.24.1, `strands-agents` 1.60.0, `mcp` 2.1.1,
`aws-cdk-lib` 2.240.0, all installed in this project's venv, plus the AWS docs
(https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html).

| Fact | Evidence |
|---|---|
| `BedrockAgentCoreApp`, `BedrockAgentCoreContext`, `RequestContext` are the SDK's top-level exports | `bedrock_agentcore/__init__.py` |
| `BedrockAgentCoreApp` extends **Starlette**; the handler is registered with `@app.entrypoint`; handlers take a payload dict and return a dict | `bedrock_agentcore/runtime/app.py` |
| Runtime subpackage: `app`, `context`, `models` (incl. `PingStatus`), `agent_core_runtime_client` | directory listing |
| Memory subpackage: `client`, `session`, `controlplane`, `constants`, `integrations`, `models` | directory listing |
| An **`identity`** subpackage exists (`auth.py`) | directory listing |
| An **`evaluation`** subpackage exists (client, integrations, span collectors) | directory listing |
| A **`gateway`** subpackage exists (`client.py`, `integrations/strands/`) — Gateway IS part of this SDK (1.24.1+) | directory listing |
| `bedrock_agentcore.gateway.integrations.strands.plugins.AgentCoreToolSearchPlugin` — semantic tool discovery plugin for Strands | `gateway/integrations/strands/plugins/agentcore_tool_search/plugin.py` |
| `strands.tools.mcp.MCPClient` — context manager; `start()` opens a background thread, `load_tools()` calls the server's `tools/list` | `strands/tools/mcp/mcp_client.py` |
| `mcp_proxy_for_aws.client.aws_iam_streamablehttp_client(endpoint, aws_service, aws_region)` — SigV4-signed streamable HTTP transport | `mcp_proxy_for_aws/client.py` (installed v1.7.0) |
| CDK has `aws_bedrockagentcore` with L2 `Gateway`, `GatewayTarget`, `CfnGateway`, `CfnGatewayTarget`, `CfnMemory`, `CfnRuntime` | `aws_cdk/aws_bedrockagentcore/__init__.py` |
| `CfnGateway.attr_gateway_url`, `.attr_gateway_identifier`, `.attr_gateway_arn` | class properties |
| L2 `Gateway.gateway_url`, `.gateway_id`, `.gateway_arn`, `.grant_invoke(grantee)` | `IGateway` interface |
| `CfnRuntime.attr_agent_runtime_id`, `.attr_agent_runtime_arn`, `.attr_workload_identity_details` | class properties |
| `CfnRuntime` supports `environment_variables` (max 50) — used to pass `AGENTCORE_GATEWAY_URL` to the container | `CfnRuntime.__init__` |
| `CfnMemory.attr_memory_id`, `.attr_memory_arn` | class properties |
| `CfnMemory` required: `Name` (`^[a-zA-Z][a-zA-Z0-9_]{0,47}$`), `EventExpiryDuration` (3–365 days) | mirrored from a working stack |
| `CfnRuntime` required: `AgentRuntimeName`, `AgentRuntimeArtifact`, `RoleArn`; `NetworkConfiguration` optional | verified in installed SDK |
| The runtime execution role must be assumable by `bedrock-agentcore.amazonaws.com`, **not** `bedrock.amazonaws.com` | asserted in a working stack's unit tests |
| Invoke URL shape: `https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{id}/invocations` | working stack |
| Gateway URL shape: `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp` | AWS docs + builder center article |
| `CfnGatewayProps` required: `authorizer_type`, `name`, `role_arn` | `CfnGatewayProps.__init__` |
| L2 `Gateway` defaults: MCP protocol, Cognito M2M inbound auth; `GatewayAuthorizer.using_aws_iam()` for IAM inbound | `GatewayProps` docstring |
| L2 `Gateway.add_lambda_target(id, lambda_function, tool_schema, ...)` — convenience method; auto-grants `lambda:InvokeFunction` to the gateway role | `Gateway.add_lambda_target` signature |
| `ToolSchema.from_inline([ToolDefinition(name, description, input_schema=SchemaDefinition(...))])` — inline Lambda tool schema | `ToolSchema.from_inline`, `ToolDefinition`, `SchemaDefinition` |
| Gateway target Lambda event: `event` is a map of inputSchema properties to values; `context.client_context.custom["bedrockAgentCoreToolName"]` carries the prefixed tool name; return `{"statusCode": 200, "body": json.dumps(result)}` | AWS docs (gateway-add-target-lambda.html) |
| A Gateway CAN front an existing MCP server (`add_mcp_server_target(endpoint=...)`) — auto-discovers tools via `tools/list` sync | CDK docs `McpServerTargetConfigurationProperty` |
| `bedrock-agentcore` 1.24.1, `strands-agents` 1.60.0, `mcp-proxy-for-aws` 1.7.0, `mcp` 2.1.1 resolve from normal PyPI | `uv.lock` |

## Unverified — deploy-time only

| Assumption | Why it is risky |
|---|---|
| Whether Gateway is available in `us-east-1`, and in which regions it is not | Region availability is exactly the kind of fact that fails at deploy time |
| **Pricing** for Runtime, Memory, and Gateway | Never checked on this project. `RESULTS.md` asks for cost per session, so this must be known before the eval can report it |
| The CDK deployment role needs `iam:CreateServiceLinkedRole` for AgentCore service-linked roles | Stated in the CDK module docs; not yet tested with this account |

## How to close this file out

Work the second table one row at a time, replacing each with the documentation
section that confirms it — the way the sibling project's spec notes did. Until a
row moves up into the verified table, treat the Gateway as unbuilt.
