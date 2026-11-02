# Decisions

Short log of choices that would otherwise be re-litigated. Newest last.

## 2026-10-13 · The Gateway is the deliverable, not the agent

A working Runtime + Memory deployment already exists in a sibling project, so
reproducing it teaches nothing. The gap is Gateway: it is the one part of the
AgentCore trio that has never been used, and it is what the job descriptions
currently ask for by name. So the agent here is deliberately boring, and the
Gateway is where the work is.

## 2026-10-13 · Tools are not imported into the agent

The echo tool now lives behind the Gateway as a Lambda target — it was
removed from `agent/tools/` so the repo has no imported tools at all. An
imported tool cannot be revoked without redeploying the agent, and its
endpoint and credentials live in the agent's memory — which is exactly the
arrangement this repo exists to remove.

## 2026-10-13 · Gateway via CDK, not the control plane

`aws_cdk.aws_bedrockagentcore` exposes `CfnGateway` and `CfnGatewayTarget`, so the
Gateway can be declared alongside the Runtime in one stack instead of being created
by a separate control-plane script. One stack means one deploy, one teardown, and
no drift between what CDK thinks exists and what a script created.

Revisit if the auth configuration turns out to need control-plane calls the
constructs do not expose — that is an open question in `API-NOTES.md`.

## 2026-10-13 · No account id anywhere in the repo

Region only, in `infra/app.py` and `cdk.json`. Account ids, ARNs, and profile names
live in `LOCAL.md`, which is gitignored, because this repo is intended to be
publishable and an account id is a durable identifier that costs nothing to keep
out.

## 2026-10-13 · Fail visibly rather than plausibly

`just eval` exits non-zero; the entrypoint returns an explicit error when
`MODEL_ID` is unset; the harness prints "not implemented" and returns 2. A scaffold
that quietly succeeds is worse than one that fails, because the success gets
mistaken for the work being done.

## 2026-10-13 · Cold start reported separately from warm latency

`evals/README.md` forbids averaging them together. Cold start is usually the number
that decides whether a design is deployable, and averaging it into p50 hides it.

## 2026-10-13 · IAM inbound auth, not Cognito

The L2 `Gateway` construct defaults to Cognito M2M inbound auth — it creates a
User Pool and client on your behalf. That works, but it means the agent needs a
client secret or a token-fetch step to call the Gateway. IAM inbound auth
(`GatewayAuthorizer.using_aws_iam()`) needs no secret: the Runtime role signs
requests with SigV4, and the Gateway validates the signature. The agent's only
credential is the role it already runs as — which is the thesis of this repo:
no tool endpoint or credential lives in the agent.

## 2026-10-13 · Lambda target, not MCP Server target

A Lambda target is fully self-contained in the CDK stack: the function, the
tool schema, and the IAM grant all live in one deploy. An MCP Server target
would front an external HTTPS endpoint (the sibling `mcp-stateless` repo),
linking the two projects — but it adds an external dependency, an HTTPS
endpoint, and OAuth2 setup that the thesis does not require. Revocation is
the point: deleting a Gateway target is a one-operation revoke, and a Lambda
target keeps that demonstration clean.

## 2026-10-13 · Plain MCPClient load_tools, not semantic search

`AgentCoreToolSearchPlugin` is the official bedrock_agentcore→Strands
integration: it derives intent via an LLM call per invocation, then searches
the Gateway's tool catalog for relevant tools. That is the right tool for
hundreds of tools. With one tool it adds an extra LLM call (cost + latency)
for no benefit. `MCPClient.start()` + `load_tools()` lists all gateway tools
once; the agent calls them only through the Gateway's `/mcp` endpoint. The
thesis — tools live behind Gateway, zero direct calls — is fully tested
either way.

## 2026-11-01 · Re-baseline to current SDKs, and label the old numbers historical

`bedrock-agentcore` 1.25.2, `strands-agents` 1.63.0, `aws-cdk-lib` 2.275.0, and
the AgentCore CLI 1.0.1 (`@aws/agentcore`). The 2026-10-13 measurements stay in
`RESULTS.md` with an explicit historical marker rather than being deleted: they
were real, they are just not claims about this tree. A reviewer can still see the
before/after.

## 2026-11-01 · Spans go to CloudWatch; Phoenix is the second, local reader

The Runtime starts under `opentelemetry-instrument` with ADOT and unified
telemetry, so every model and tool span lands in the agent's own log group —
the same group batch evaluation reads. Phoenix is not a parallel production
sink; it is a local second reader used only by the comparison, fed by the same
Strands spans through `PHOENIX_OTLP_ENDPOINT`. One instrumentation path, two
readers, and no eval traffic sent to CloudWatch from the laptop.

## 2026-11-01 · Evaluation needs a control that can fail

The old pass rate was a substring check that a no-tool run also satisfied. The
replacement has three independent signals: built-in judges over real spans, a
deterministic code evaluator (`EchoFidelity`) over ground-truth assertions, and
`Builtin.FailureAnalysis` for grouped insights. Scenario `s11` carries a
deliberately false assertion; if it ever reports PASS, the evaluator is broken
and the run is invalid. That is cheaper and more honest than trusting a score.

## 2026-11-01 · Faithfulness is cut from the comparison, not faked

The only tool is `echo`, so there is no retrieved context for a faithfulness
judge to be faithful to. The Phoenix comparison measures two pairs
(correctness, and a custom rubric written identically for both platforms), not
three. Adding a retrieval-shaped tool is phase 4 work; claiming groundedness
with an echo tool would be the same overclaim the old pass rate made.

## 2026-11-01 · Forward the user token in an interceptor, not an outbound provider

Both target types reject `JWT_PASSTHROUGH`, and the hosted consent portal
rejects Cognito, so the outbound-credential path is a dead end for this IdP.
The Gateway already validates the user's token; a REQUEST interceptor copies
that `Authorization` value into an allowlisted header, the target's API Gateway
accepts only the Gateway role's IAM credentials, and the MCP Lambda calls
Cognito `GetUser` to resolve the principal. This keeps Cognito, avoids a second
IdP, and makes the tool see the user rather than a shared service account.

The tradeoff is explicit: the interceptor is trusted code on the request path,
and the target must stay IAM-only so the forwarded header is not a public
bearer gate. Target deletion remains the revocation control.

## 2026-11-02 · Policy is a Gateway attachment, not agent code

The policy engine is declared in the same CDK stack and attached to the Gateway
in `ENFORCE` mode. The agent sends a policy session id and translates a denial
into a refusal, but it does not evaluate the rule. That keeps the limit
revocable and observable without redeploying the Runtime.

The baseline is a broad permit plus narrow forbids. Cedar is default-deny, so a
policy engine attached without a permit hides the entire catalog; the baseline
permit restores discovery and leaves the limits to the forbids. The static
analyzer calls both the baseline "overly permissive" and the temporal forbid
"overly restrictive", so those two policies use `IGNORE_ALL_FINDINGS` while the
parameter rule keeps `FAIL_ON_ANY_FINDINGS`.

## 2026-11-02 · Commit the generated policy, not a deploy-time generation

`StartPolicyGeneration` produced the quota rule from a prompt, but invoking it
during deploy would make the stack depend on a model call and leave the policy
invisible in review. The generated Cedar is committed, with the prompt and the
generation date beside it. The ARN is a CDK token, so no account id enters the
repo.

## 2026-11-02 · `policy-quota___spend` exists to make the rule observable

The original echo tool has one string parameter, and Cedar here has no string
length function; a generated `like "????...*"` rule was probed and did not behave
as a length check. Rather than fake a length rule, the stack adds a tiny
parameterised spend tool whose `amount` is an integer. The policy denies
`amount > 100`; the tool itself enforces nothing.

## 2026-11-02 · Code Interpreter and Browser go behind a Gateway MCP target

The SDK exposes Code Interpreter and Browser as direct Strands tools; that
would put two tools outside the Gateway and break the phase's control. A small
MCP Runtime (`toolbox`) wraps one operation from each and the Gateway reaches it
as an ordinary MCP server target. The agent still knows only a Gateway URL, and
the policy engine sees every call.

The toolbox deliberately returns text, not live-view URLs or session
credentials. Browser automation is one CDP navigation plus a title/text read;
this is a tool-surface proof, not a browser agent.

## 2026-11-02 · Step Functions uses the built-in connector, not a Lambda proxy

The connector target pins `StartExecution` to one Standard state machine and
removes `stateMachineArn` from the tool schema. That makes the workflow choice
an infrastructure decision, not a model decision. A Lambda proxy would have
been easier to test but would have hidden the thing the connector buys: the
Gateway owns schema, authentication and the pinned parameter.

## 2026-11-02 · OpenAPI uses an API-key provider and a Function URL

The OpenAPI target is a Lambda Function URL whose Lambda verifies the key stored
in AgentCore Token Vault. The key is generated by CloudFormation; the Lambda
reads the same secret. The alternative - IAM outbound auth - is rejected for
OpenAPI targets, and a public endpoint would have made the auth boundary
decorative.

## 2026-11-02 · Target validation order is part of the design

Two deploy failures were ordering bugs, not API limits: a generated forbid was
validated before its baseline permit existed ("Overly Restrictive"), and an MCP
runtime failed to start when its role lacked `logs:PutResourcePolicy`. The stack
now makes the quota policy depend on the baseline and grants the logging actions
the runtime calls during startup.

## 2026-11-02 · Registry uses `AGENT` + custom, not a fake A2A card

AgentCore Registry's `AGENT` record accepts A2A agent cards, HTTP descriptors or
the custom descriptor. The Runtime is an HTTP endpoint that does not implement
A2A, so publishing an A2A card would be a metadata lie. The record therefore
uses `AGENT` + `custom`, carrying the deployed Runtime ARN, Gateway id/URL,
model and tool names. A fresh caller can still find it semantically; an A2A
client would correctly see that this is not an A2A service.

The tool record is `MCP` because the Gateway really is an MCP server, with the
same prefixed tool names and input schemas the agent sees.

## 2026-11-02 · Publish is separate from deploy; discovery is separate from publish

`CfnRegistryRecord` creates a `DRAFT` and CloudFormation cannot express the
submit transition. The stack creates the records and configures
`APPROVE_ALL`; `just registry-publish` performs the one admin submit. The
acceptance check (`just registry-check`) uses only the data-plane discovery API
and cannot approve itself, so "a fresh caller finds it" is not circular.

The tools payload is a projection maintained in the stack, not a live
`tools/list` at read time. That is enough for discovery, but adding or removing
a Gateway target requires redeploying the record; the Runtime ARN, by contrast,
is generated from the deployed CDK attribute and cannot drift.

## 2026-11-02 · A second Gateway is the A/B front door, not a change to the tool Gateway

The Gateway that fronts tools is MCP-only and carries a Cognito JWT authorizer,
the request interceptor and the policy engine. An AgentCore config-bundle A/B
test needs an HTTP Runtime target and its own routing surface; adding that to
the tool Gateway would either remove its MCP guarantee or mix two unrelated
traffic paths. The stack therefore adds `agentcore-agent-front`, an
IAM-authenticated Gateway with one `httpRuntime` target. The agent's tools still
arrive only from the original Gateway.

The L2 `Gateway` construct defaults to MCP, so the front door explicitly clears
`ProtocolType` and `ProtocolConfiguration`. Without that, the Runtime target
synthesizes but is not an HTTP gateway.

## 2026-11-02 · Harness gets the token per invocation, not in its config

The Harness could be configured with an `agentCoreGateway` tool and an OAuth
provider, but that adds a machine client, a secret and another credential
provider for a five-session comparison. Instead, `invoke_harness` receives one
`remote_mcp` tool per call with the same Gateway URL and a fresh Cognito access
token in its headers. The Harness still exercises the same Gateway, JWT
validation and tool catalog as the Runtime, and the token is never stored in a
CloudFormation template or a Harness resource.

The trade-off is that the experiment script owns the credential lifetime. The
comparison is a case study, not a production harness deployment.

## 2026-11-02 · The prompt A/B is a config bundle, and the hook is last-write-wins

The control and treatment differ only in `system_prompt`, so they are two
immutable configuration-bundle versions, not two Runtime deployments. The
Runtime registers a `BeforeModelCallEvent` callback that reads
`BedrockAgentCoreContext.get_config_bundle()` and applies `system_prompt`.
Outside an A/B session the context returns `{}` and the deployed baseline stays.

A bundle read failure logs and falls back to the baseline instead of failing the
user's turn: the prompt experiment is an optimization path, not a new hard
dependency. The online evaluation config uses built-in `Correctness` and
`GoalSuccessRate` because A/B metrics must be numerical.

## 2026-11-02 · Cost means Bedrock inference cost, not platform cost

The harness comparison records wall time and tokens, then prices tokens at the
published DeepSeek v3.2 Standard on-demand rates in `us-east-1` ($0.62 / 1M
input, $1.85 / 1M output). It does not include Runtime, Harness, Gateway, Memory
or log charges. Calling that number "cost per session" would overclaim, so
`RESULTS.md` labels it inference cost.

## 2026-11-02 · The A/B front door applies to all of its traffic

The front-door Gateway has one target, so the experiment does not need a
`gatewayFilter`. A `/agent/*` target filter was tried first and did not intercept
`/agent/invocations`; the Runtime never received a bundle ref. Removing the
filter made the reference arrive and kept the experiment scoped by the fact that
the front door has no other target.

The Runtime and Gateway roles also had to be granted
`bedrock-agentcore:GetConfigurationBundleVersion`. Without it, the first bundle
fetch failed inside the tool registry and the front door reported a generic HTTP
424; the error only appeared in CloudWatch. The permission is now explicit on
the runtime role and both Gateway roles.


## 2026-11-02 · Stop A/B baggage at the Runtime boundary

The first A/B attempts failed with `MCPError: Config bundle fetch failed` while
the Runtime was listing Gateway tools, not while evaluating a turn. The
OpenTelemetry composite propagator was injecting the front-door A/B baggage
into outbound MCP requests; the tool Gateway then tried to resolve the bundle
for tool-description overrides and returned a JSON-RPC error. The Runtime now
sets `OTEL_PROPAGATORS=tracecontext`: inbound baggage is still read by
`BedrockAgentCoreApp`, but outbound MCP calls carry trace context only. The
experiment stays at the front door instead of leaking into every downstream
tool call.

## 2026-11-02 · Teardown proof names what CDK cannot delete

`cdk destroy` removes the stack, but CloudWatch retains Lambda and AgentCore
log groups, and runtime-created service resources (A/B tests, configuration
bundles, Browser/Code Interpreter sessions) are outside CloudFormation.
`just down` now runs the phase-6 cleanup, destroys the stack, then deletes the
app log groups. `just teardown-check` queries the service APIs and fails if any
application resource remains.

The shared `CDKToolkit` stack, S3 asset buckets and ECR asset repository are
account-level bootstrap infrastructure. They are reported, not deleted: they
predate this application and other CDK apps may depend on them. The ship claim
is therefore "no application resource remains billing", with the retained
bootstrap storage named in `RESULTS.md`.

## 2026-11-02 · The Runtime's Bedrock grant stays broad, because the model is a parameter

The runtime role grants `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on `resources=["*"]`. That was a
leftover TODO — "narrow to specific model ARNs once the model is fixed" — and
the premise is wrong: the model is **not** fixed. `model_id` is a CDK context
parameter (`cdk deploy -c model_id=...`, default `deepseek.v3.2`) because the
judge model and the agent model have to be swappable to run the evaluator
comparison. Pinning one foundation-model ARN, or one inference-profile ARN,
would break the documented deploy path and would fail at runtime rather than at
synth — the failure this repo is designed to avoid.

The grant's real scope is narrower than the wildcard suggests: the only
resources `bedrock:InvokeModel` can reach are model and inference-profile ARNs,
so this grants invoke on whatever model the deploy selects and nothing else.

Tightening it is deliberately an unscheduled change, and it needs a deploy to
verify, not a synth. Two paths if that ever becomes worth doing:

1. **Drop the knob.** Pin a single inference-profile ARN, remove `model_id`
   from context and from the evaluator comparison, and accept that the judge
   model can no longer be changed per deploy.
2. **Keep the knob, scope the ARN set.** Grant
   `arn:${partition}:bedrock:${region}:${account}:inference-profile/${model_id}`
   plus the foundation models it routes to, and re-run `just identity-check`
   and `just eval` to prove the route still resolves — a cross-region inference
   profile invokes foundation models in other regions, which is where a
   too-tight policy breaks in production and not in review.

Until then the broad grant is a stated design consequence, not an oversight, and
this entry is what a reviewer should read instead of the missing narrowing.
