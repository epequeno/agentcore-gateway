# API notes — what is verified, and what is still an assumption

**Status: mostly verified.** The first table was read out of the installed SDKs
and the AWS docs. The second table holds the few facts that can only be resolved
at deploy time or against the pricing page.

This file exists because the last project in this portfolio was scaffolded from a
release-candidate blog post, and verifying it against the real specification later
found **ten-plus divergences** — including `initialize` having been removed
entirely. Assumptions about cloud APIs are expensive. Record them, then check them.

## Verified locally

Read from `bedrock_agentcore` 1.25.2, `strands-agents` 1.63.0, `aws-cdk-lib`
2.275.0 and `@aws/agentcore` 1.0.1, plus the AWS docs
(https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/). The rows below
that name 1.24.1 were verified against the previous pin and re-checked after the
2026-11-01 upgrade.

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
| `bedrock-agentcore` 1.25.2, `strands-agents` 1.63.0, `aws-cdk-lib` 2.275.0, `mcp-proxy-for-aws` 1.7.0 resolve from normal PyPI | `uv.lock` |
| Runtime, Memory, Gateway and Registry deploy **and invoke** in `us-east-1` | the 2026-10-13 Runtime deploy and the 2026-11-02 Registry deploy. Deploy evidence rather than documentation — other regions remain unchecked |
| The AgentCore CLI is npm-only: `npm install -g @aws/agentcore`; v1.0.1 reports `agentcore --version` | installed 2026-11-01; the pip `bedrock-agentcore-starter-toolkit` is a different, older surface |
| `bedrock_agentcore.evaluation` ships `EvaluationClient`, `BatchEvaluationRunner`, `CloudWatchDataSourceConfig`, `BatchEvaluationRunConfig`, `BatchEvaluationResult` and `fetch_evaluation_events` | installed 1.25.2; batch evaluation is implemented in the SDK, not just the API |
| Code-based evaluator Lambda contract: `@custom_code_based_evaluator()` wraps `(EvaluatorInput, context) -> EvaluatorOutput`; the raw response is `{"label", "value", "explanation"}` or `{"errorCode", "errorMessage"}` | `evaluation/custom_code_based_evaluators/` |
| CDK 2.275.0 exposes L2 `Evaluator` and `EvaluatorConfig.code_based(lambda_function, timeout)` / `.llm_as_a_judge(instructions, model_id, rating_scale, inference_config)`; levels are `SESSION`, `TRACE`, `TOOL_CALL` | `aws_bedrockagentcore.Evaluator*` |
| `StartBatchEvaluation` reads CloudWatch Logs: `cloudWatchLogs.serviceNames` (exactly 1), `logGroupNames` (1–10) and `filterConfig.sessionIds`/`timeRange` | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations-start.md |
| Batch evaluation ground truth is per-session `evaluationMetadata.sessionMetadata[{sessionId, groundTruth.inline.{turns, assertions}}]` | same page; `BatchEvaluationRunner._transform_ground_truth` |
| Runtime spans need ADOT >= 0.18.0 and `UNIFIED_TRACES_DESTINATION_ENABLED=true` to land in the agent's own log group (`.../runtimes/<id>-DEFAULT`, stream `spans`); ADOT is started with `opentelemetry-instrument` | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.md |
| Unified-telemetry session content is inline span events (`gen_ai.user.message`, `gen_ai.choice`, `gen_ai.tool.message`); span type is `gen_ai.operation.name` | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks-strands.md |
| Transaction Search is account-level and required: X-Ray destination must be `CloudWatchLogs`, with a Logs resource policy for `xray.amazonaws.com` | enabled in this account on 2026-11-01 with `scripts/enable_transaction_search.sh` |
| AgentCore Runtime rejects amd64 images: "Supported platforms: [arm64]" | deploy failure on 2026-11-01; `AgentRuntimeArtifact.from_asset(platform=LINUX_ARM64)` deployed successfully |
| With `UNIFIED_TRACES_DESTINATION_ENABLED=true` and ADOT 0.22.0, spans land in `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT` stream `spans`, and Runtime injects `session.id` on every span | verified in CloudWatch on 2026-11-01: `invoke_agent`, `chat`, `execute_tool` spans all carried the session id |
| A Gateway Lambda target appears to MCP clients as `echo-target___echo` (three underscores) | verified in the agent's tool catalog and in `execute_tool` spans |
| Batch evaluation over 11 sessions / 26 turns completes and returns per-turn events under `gen_ai.evaluation.*`; insights come back under `insightResults` | run `gateway_eval_5834fe93-df2c665734`, 2026-11-01 |
| Judge token usage is not exposed by the evaluation API | batch result and events carry scores and explanations only |
| Cognito's OIDC discovery document is on `https://cognito-idp.<region>.amazonaws.com/<poolId>/.well-known/openid-configuration`, not on the hosted-UI domain | fetched live 2026-11-01; the hosted UI serves authorize/token |
| A Gateway JWT authorizer accepts Cognito access tokens with `allowedClients=[clientId]`; unauthenticated MCP calls return HTTP 401 `Missing Bearer token` | `just identity-check` |
| 3LO outbound auth on a target requires MCP `2025-11-25`; the default revision rejects it | deploy failure 2026-11-01; fixed via `protocolConfiguration.mcp.supportedVersions` on the L1 Gateway |
| Cognito access tokens omit `aud`; a V2 pre-token trigger can add `aud`, `email`, `email_verified` via `claimsAndScopeOverrideDetails.accessTokenGeneration` | decoded tokens before/after the trigger |
| `UpdateUserPoolClient` can leave the app client without a usable secret; rotate with `AddUserPoolClientSecret` and resync the OAuth2 provider | portal code exchange failed until resynced |
| Cognito authentication succeeds, but the hosted consent portal rejects its `/callback` with `SignInRejected`; CloudTrail shows only the error code | `CompleteConsentPortalSignIn` events, 2026-11-01 |

| A Gateway REQUEST interceptor sees the incoming `Authorization` header when `inputConfiguration.passRequestHeaders=true`; it returns `mcp.transformedGatewayRequest.headers` | deployed interceptor; the header reached the MCP Lambda |
| Interceptor-provided `Authorization` is propagated to the target automatically; any other custom header must be allowlisted in the target's `metadataConfiguration.allowedRequestHeaders` | https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html; deployed `x-agentcore-user-token` |
| An MCP server target with `GATEWAY_IAM_ROLE` requires `credentialProvider.iamCredentialProvider.service` and `.region`; API Gateway HTTP API uses `HttpIamAuthorizer` | `CreateGatewayTarget` validation; synthesized route |
| The Gateway execution role needs `execute-api:Invoke` on `arn_for_execute_api("POST", "/mcp")` to call an IAM-authorized HTTP API target | deployed target reached the Lambda |
| Target creation calls the MCP server's `tools/list` before any user request exists, so discovery must work without a token; `tools/call` can still require one | deploy failure "invalid user token", then successful deploy |
| `JWT_PASSTHROUGH` is rejected for both OpenAPI and MCP server targets in this account/version, despite the outbound-auth table listing it | deploy failures, 2026-11-01 |
| Cognito `GetUser` with an access token returns `Username` and attributes including `sub` and `email`; it is a server-side signature/expiry check the MCP Lambda can use | deployed tool returned the token `sub` |
| The hosted consent portal returns `SignInRejected` for a Cognito primary IdP | `CompleteConsentPortalSignIn`, 2026-11-01 |

| CDK 2.275.0 exposes L2 `PolicyEngine` and `GatewayPolicyEngineConfig`; `Gateway(policy_engine_configuration=...)` attaches it, and `PolicyEngineMode.ENFORCE` is the enforcing mode | `aws_bedrockagentcore` module; deployed stack |
| The Gateway execution role needs `bedrock-agentcore:AuthorizeAction`, `PartiallyAuthorizeActions`, `GetPolicyEngine`; temporal policies additionally need `GetWorkloadAccessToken` scoped to the workload-identity directory | deployed policies; policy docs (`policy-permissions.md`) |
| A broad `permit(principal, action, resource == Gateway)` is rejected by `FAIL_ON_ANY_FINDINGS` as "Overly Permissive"; a conditional temporal forbid is rejected as "Overly Restrictive". Validation is per policy, not a combined analysis | deploy failures 2026-11-02; `IGNORE_ALL_FINDINGS` on those two policies |
| `StartPolicyGeneration` returns status `GENERATED` and assets under `ListPolicyGenerationAssets`; the generated statement for "amount greater than 100" is `context.input.amount > 100` with no findings | generation `quota_limit-...`, 2026-11-02 |
| Cedar's `like` in this schema supports `*` and literal matches, but `?` did not act as a single-character wildcard in a live probe (`like "???"` did not match `aaa`) | temporary policies, 2026-11-02; do not use generated `?` patterns for length rules |
| Temporal policies require `x-amzn-bedrock-agentcore-policy-session-id` (1–128 chars, `[A-Za-z0-9-]`) and evaluate only events from that session; the Gateway binds the session to the authenticated principal | `just policy-check`; `policy-session-based-temporal.md` |
| A policy deny returns HTTP 200 with JSON-RPC error `-32002` and `Tool Execution Denied ... policy enforcement [policy-id]`, not an HTTP 4xx | direct MCP call and agent refusal, 2026-11-02 |
| Policy decisions publish to `AWS/Bedrock-AgentCore` as `AllowDecisions` / `DenyDecisions` with `Policy`, `TargetResource`, `OperationName`, `PolicyEngine`, and `Mode` dimensions | `get_metric_statistics`, 2026-11-02 |
| `CfnRuntime.protocolConfiguration` is the string enum `MCP` / `HTTP` / `A2A` / `AGUI`, not the three-field protocol object used by `CreateAgentRuntime`; MCP defaults to port 8000 and path `/mcp` | CDK synth validation; deployed toolbox runtime |
| The `mcp` 2.x package removed `mcp.server.fastmcp`; a FastMCP server must pin `mcp<2` (the toolbox pins 1.28.1) | container crash loop, 2026-11-02 |
| An MCP server on AgentCore Runtime is reachable at `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<percent-encoded-arn>/invocations?qualifier=DEFAULT`; a Gateway MCP server target can use that URL | deployed `toolbox` target |
| OpenAPI targets accept API-key and OAuth outbound auth; the CDK default IAM credential provider fails at runtime | CDK docs; deployed API-key target |
| `ApiKeyCredentialProvider(api_key=secret.secret_value)` stores the full generated secret string, including a JSON envelope, in Token Vault; the target's default header prefix is `Bearer ` | token-vault secret read back, 2026-11-02 |
| `ApiKeyCredentialLocation.header()` defaults to the `Authorization` header and `Bearer ` prefix; an OpenAPI target sends both unless overridden | synthesized target; Lambda log/key probe |
| The built-in Browser and Code Interpreter resources live in account `aws`, not the caller's account (`...:aws:browser/aws.browser.v1`, `...:aws:code-interpreter/aws.codeinterpreter.v1`) | `StartBrowserSession` / `StartCodeInterpreterSession` AccessDenied messages, then successful calls |
| The Step Functions connector target uses `mcp.connector.source.connectorId="states"` and pins `stateMachineArn` per `configurations[].parameterValues`; the tool is prefixed `<target>___<Operation>` | deployed `workflow___StartExecution` |
| Gateway publishes `AWS/Bedrock-AgentCore` `Invocations` with `TargetType` values including `LAMBDA` and `CONNECTOR`, and per-tool `Name` dimensions for Lambda/OpenAPI/MCP targets | CloudWatch `list_metrics`, 2026-11-02 |
| Registry is the new `agent-registry` namespace, not the discontinued `bedrock-agentcore` namespace: control plane `agent-registry-control`, data plane `agent-registry`; both endpoints exist in `us-east-1` | live clients and a successful registry create, 2026-11-02 |
| Registry creation is asynchronous: `CreateRegistry` returns `CREATING`, and records cannot be created until `GetRegistry` says `READY`; the first deploy's registry took about 60 seconds | smoke registry and stack deploy, 2026-11-02 |
| `CfnRegistryRecord` creates a record in `DRAFT`; `SubmitRegistryRecordForApproval` is a separate transition. With `ApprovalConfiguration.autoApprovalRules=["APPROVE_ALL"]`, one submit returns `APPROVED` | control-plane API, 2026-11-02 |
| CDK exposes `aws_agentregistry` with `CfnRegistry` and `CfnRegistryRecord`; the record type `AGENT` accepts a `Custom` descriptor for a non-A2A Runtime | synthesized template and deployed records |
| MCP server descriptor schema `2025-12-11` requires name, description and version; description is capped at 100 characters and name must be `namespace/name` | schema validation failure, then a corrected deploy |
| Registry search is a data-plane hybrid of semantic and keyword search; only records whose latest revision is `APPROVED` are discoverable, and indexing is eventually consistent | `SearchDiscoverableRegistryRecords` + retry/wait, 2026-11-02 |
| `InvokeHarness` returns a `stream` of content-block events plus `metadata.usage`; a `remote_mcp` tool with an `Authorization` header reaches the existing JWT Gateway, so the Harness and Runtime can share one tool catalog | live `invoke_harness`, 2026-11-02 |
| A Harness is a config-defined managed Runtime under the hood: `CfnHarness` creates a `harness_<name>` Runtime, and its endpoint is `READY` without a container image | `agentcore harness get`, 2026-11-02 |
| Configuration-bundle components are keyed by Runtime ARN; `CreateConfigurationBundle` returns an immutable `bundleArn` plus `version` (the version id used by an A/B test) | configuration-bundle create + A/B create, 2026-11-02 |
| A config-bundle A/B test needs an HTTP Runtime target on a Gateway, not an MCP target; the front-door Gateway must have `protocolType` unset, and the target is `TargetConfiguration.Http.AgentcoreRuntime` | synthesized template and successful front-door target |
| `CreateABTest` requires a roleArn and rejects a gateway with "Access denied for gateway ... Verify the IAM role has the required permissions" until the role has `bedrock-agentcore:GetGateway`, `GetGatewayTarget`, `ListGatewayTargets`, the rule actions, bundle/evaluator reads and CloudWatch Logs query actions | failed create, then corrected role policy |
| Online evaluation config names must start with a letter and contain only alphanumerics and underscores; a hyphen is rejected at synth time | CDK validation, 2026-11-02 |
| A/B evaluators must be numerical; built-in `Correctness` and `GoalSuccessRate` are accepted, custom judges need a numerical rating scale | A/B prerequisites doc + `CreateABTest` |
| Runtime SDK `BedrockAgentCoreContext.get_config_bundle()` applies a bundle's `system_prompt`; outside a bundle-routed request it returns `{}` | installed SDK + runtime hook test |
| The Runtime's execution role **and** the Gateway service role that resolves the bundle need `bedrock-agentcore:GetConfigurationBundleVersion`; without it the MCP tool registry fails with "Config bundle fetch failed" and the front door returns HTTP 424 | CloudWatch error + corrected IAM policy |
| A `gatewayFilter.targetPaths=["/agent/*"]` did not intercept requests to `/agent/invocations` in this deployment; omitting the filter made the bundle ref arrive (`Received config bundle ref`) | A/B run with and without the filter |
| OTel baggage must not propagate to outbound MCP calls while an A/B bundle is active; with the default composite propagator the tool Gateway saw the bundle and returned a JSON-RPC error, which surfaced as `MCPError: Config bundle fetch failed`. Setting `OTEL_PROPAGATORS=tracecontext` keeps the front-door baggage inbound but stops outbound leakage | A/B one-session probe before/after the env change |
| Runtime session ids passed through the front door must be at least 33 characters; a 22-character id returned HTTP 400 before the Runtime was reached | front-door probe |


## Unverified — deploy-time only

| Assumption | Why it is risky |
|---|---|
| **Platform pricing** for Runtime, Harness, Memory and Gateway | `RESULTS.md` reports Bedrock inference cost only. The per-second/per-request platform charges were not checked; do not present inference cost as total cost |
| **A/B statistical power** | Twenty sessions is a case study. The service can return means without significance; the result must say which happened |
| The CDK deployment role needs `iam:CreateServiceLinkedRole` for AgentCore service-linked roles | Stated in the CDK module docs; not yet tested with this account |

## How to close this file out

Work the second table one row at a time, replacing each with the documentation
section that confirms it — the way the sibling project's spec notes did. The
Gateway, Identity, evaluation, policy, heterogeneous target, Registry,
Harness and prompt A/B paths are now verified. The remaining deploy-time risk is
platform pricing: `RESULTS.md` reports Bedrock inference cost separately and
does not call it total cost.
