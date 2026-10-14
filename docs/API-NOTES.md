# API notes — what is verified, and what is still an assumption

**Status: partially verified.** Everything in the first table was read out of the
installed SDKs on this machine, so it is reliable. Everything in the second table
is unverified and must be checked against the AWS documentation before the Gateway
is implemented.

This file exists because the last project in this portfolio was scaffolded from a
release-candidate blog post, and verifying it against the real specification later
found **ten-plus divergences** — including `initialize` having been removed
entirely. Assumptions about cloud APIs are expensive. Record them, then check them.

## Verified locally

Read from `bedrock_agentcore` 1.4.6 and `aws-cdk-lib` 2.240.0, both already
installed in a sibling project.

| Fact | Evidence |
|---|---|
| `BedrockAgentCoreApp`, `BedrockAgentCoreContext`, `RequestContext` are the SDK's top-level exports | `bedrock_agentcore/__init__.py` |
| `BedrockAgentCoreApp` extends **Starlette**; the handler is registered with `@app.entrypoint`; handlers take a payload dict and return a dict | `bedrock_agentcore/runtime/app.py` |
| Runtime subpackage: `app`, `context`, `models` (incl. `PingStatus`), `agent_core_runtime_client` | directory listing |
| Memory subpackage: `client`, `session`, `controlplane`, `constants`, `integrations`, `models` | directory listing |
| An **`identity`** subpackage exists (`auth.py`) | directory listing |
| An **`evaluation`** subpackage exists (client, integrations, span collectors) | directory listing |
| **There is no `gateway` subpackage** — Gateway is not part of this SDK | directory listing |
| CDK has `aws_bedrockagentcore` with `CfnGateway`, `CfnGatewayTarget`, `CfnMemory`, `CfnRuntime`, `CfnBrowserCustom`, `CfnCodeInterpreterCustom` | `aws_cdk/aws_bedrockagentcore/__init__.py` |
| `CfnGateway.attr_gateway_url`, `.attr_gateway_identifier`, `.attr_gateway_arn` | class properties |
| `CfnRuntime.attr_agent_runtime_id`, `.attr_agent_runtime_arn`, `.attr_workload_identity_details` | class properties |
| `CfnMemory.attr_memory_id`, `.attr_memory_arn` | class properties |
| `CfnMemory` required: `Name` (`^[a-zA-Z][a-zA-Z0-9_]{0,47}$`), `EventExpiryDuration` (3–365 days) | mirrored from a working stack |
| `CfnRuntime` required: `AgentRuntimeName`, `AgentRuntimeArtifact`, `NetworkConfiguration`, `RoleArn` | mirrored from a working stack |
| The runtime execution role must be assumable by `bedrock-agentcore.amazonaws.com`, **not** `bedrock.amazonaws.com` | asserted in a working stack's unit tests |
| Invoke URL shape: `https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{id}/invocations` | working stack |
| `bedrock-agentcore` 1.4.6 and `strands-agents` 1.30.0 resolve from normal PyPI | `uv.lock` |

## Unverified — check before implementing the Gateway

| Assumption | Why it is risky |
|---|---|
| Required properties and target types for `CfnGateway` / `CfnGatewayTarget` | Not read yet. Guessing these is how the last scaffold got ten divergences |
| Whether a Gateway can front an **existing MCP server** as a target | Reported by an AWS blog post (July 2026), not the docs. If true, the sibling `mcp-stateless` repo becomes a natural target — which would link the two projects |
| Inbound/outbound auth configuration, and the service-linked-role change for **workload identity** (reported as 2025-10-13) | Secondary sources only; the CLI reportedly does not configure one of the required fields, so the control plane may be needed alongside CDK |
| `bedrock-agentcore-starter-toolkit` API (`create_mcp_gateway_target`, target types such as `smithyModel`) | Seen only in a quickstart page |
| Whether Gateway is available in `us-east-1`, and in which regions it is not | Region availability is exactly the kind of fact that fails at deploy time |
| **Pricing** for Runtime, Memory, and Gateway | Never checked on this project. `RESULTS.md` asks for cost per session, so this must be known before the eval can report it |

## How to close this file out

Work the second table one row at a time, replacing each with the documentation
section that confirms it — the way the sibling project's spec notes did. Until a
row moves up into the verified table, treat the Gateway as unbuilt.
