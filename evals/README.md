# evals — span-based, with a control that can fail

`just eval` invokes every scripted session against the deployed Runtime, then
starts an AgentCore **batch evaluation** over the spans those sessions produced
in CloudWatch Logs. The pass rate is no longer a substring check: the service
scores real traces with built-in judges, and a deterministic Lambda evaluator
checks seeded assertions.

```sh
export AGENTCORE_RUNTIME_ARN=<arn>
export AGENTCORE_GATEWAY_URL=<url>
export AGENTCORE_MEMORY_ID=<id>
export AGENTCORE_EVALUATOR_ARN=<from `just deploy` outputs>
export AGENTCORE_RUBRIC_EVALUATOR_ARN=<from `just deploy` outputs>
# Identity: the Gateway now validates a Cognito JWT on every tool call.
export COGNITO_USER_POOL_ID=<from `just identity-setup`>
export COGNITO_CLIENT_ID=<from `just identity-setup`>
export IDENTITY_TEST_USER=support-user
export IDENTITY_TEST_PASSWORD=<not committed>
just eval
```

## What a valid run proves

1. **The tool surface is the Gateway.** Every `execute_tool` span in the session
   window names the Gateway target's tool (`echo-target…`). A call that bypassed
   the Gateway would have to name something else; the echo Lambda has no direct
   invoke grant.
2. **The evaluator can fail.** Scenario `s11` carries a deliberately false
   assertion (`seeded-violation-token`). The `EchoFidelity` code evaluator must
   return FAIL for it. If it returns PASS, the run is invalid and exits non-zero.
3. **Judges saw the sessions.** `Builtin.Correctness` and `Builtin.GoalSuccessRate`
   return per-turn scores, `Builtin.FailureAnalysis` returns grouped insights,
   and `GatewayRubric` is the custom LLM-as-judge used by the Phoenix comparison.

The harness mints the test user's access token with `ADMIN_USER_PASSWORD_AUTH`
and sends it as `authToken`; the agent forwards it to the Gateway. A missing
token is a configuration error, not a model-only answer. A Gateway deny is a
JSON-RPC error, so the agent must translate it into a refusal; that is checked
separately by `just policy-check`.

The harness writes the full metrics (responses, per-turn events, attribution) to
`results/batch-<id>.json`, which is gitignored. `RESULTS.md` keeps the committed
numbers.

## Sessions

- **s01–s02**: echo routing.
- **s03–s05**: multi-turn memory continuity — later turns reference earlier ones.
- **s06–s08**: repeated tool calls.
- **s09–s10**: no-tool baseline — the agent answers without inventing a call.
- **s11**: seeded violation, must FAIL the deterministic evaluator.

## Policy check

`just policy-check` is deliberately outside the scripted sessions: it makes a
quota call at the boundary (`100` allowed, `101` denied), makes two CRM calls in
one policy session (second denied), invokes the Runtime once to inspect the
model's refusal, and reads `AllowDecisions`/`DenyDecisions` from CloudWatch.
Requires `POLICY_ENGINE_ARN` and `AGENTCORE_GATEWAY_ID` in addition to the
identity exports. Design: [`docs/POLICY.md`](../docs/POLICY.md).

## Tool-surface check

`just tool-surface` authenticates with the same Cognito user and calls one tool
through every configured target:

- `echo-target___echo` - Lambda target
- `crm-mcp___getAccount` - MCP server target with JWT passthrough
- `openapi-status___fetchStatus` - OpenAPI target with API-key outbound auth
- `workflow___StartExecution` - Step Functions connector target pinned to one
  Standard state machine
- `toolbox___execute_python` and `toolbox___browse_page` - an MCP server target
  whose Runtime wraps AgentCore Code Interpreter and Browser

Tool names carry the target prefix; a direct call could not use those names.
The script also lists `AWS/Bedrock-AgentCore` metrics for the service-side
attribution record. Design and limitations: [`docs/TOOL-SURFACE.md`](../docs/TOOL-SURFACE.md).

## Phoenix comparison

```sh
export PHOENIX_OTLP_ENDPOINT=http://localhost:4318
just eval-phoenix          # same sessions, run locally, spans -> Phoenix
just phoenix-compare       # score in Phoenix, pair with AgentCore's events
```

Phoenix consumes the same Strands spans and the same Bedrock judge model. The
comparison has two pairs, not three: correctness (prebuilt on both sides) and
the custom rubric (identical instructions on both sides). Faithfulness is cut
because `echo` retrieves nothing, and a groundedness score over an echo tool
would be the same overclaim the old pass rate made. Method and limitations:
[`docs/EVAL-COMPARISON.md`](../docs/EVAL-COMPARISON.md).

## Falsification

- Any tool call that does not traverse the Gateway fails the thesis regardless of
  scores. The harness reports it and exits non-zero.
- If the seeded violation stops failing, the deterministic evaluator is broken;
  the run is rejected rather than reported.
- Cold start and warm latency are reported separately. Never averaged.
- n=11 scenarios is a case study, not a benchmark. Report it as such.

## Registry check

`just registry-publish` submits the stack-created DRAFT records
(`agentcore-gateway-agent`, `agentcore-gateway-tools`); the registry's
`APPROVE_ALL` rule makes one submit final. `just registry-check` then acts only
as a consumer: a new `agent-registry` data-plane client sends a natural-language
query and reads the approved record. It does not use the control-plane IDs or a
record name, and it compares the agent descriptor's `runtimeArn` to
`AGENTCORE_RUNTIME_ARN`. Export `AGENTCORE_REGISTRY_ARN` and
`AGENTCORE_RUNTIME_ARN` from `just deploy`. Design and limits:
[`docs/REGISTRY.md`](../docs/REGISTRY.md).

## Hosting comparison

`just hosting-compare` runs one CRM task on Harness and Runtime with the same
model, prompt and Gateway catalog. It reports wall time, token usage and
Bedrock inference cost per session. The Harness receives the Gateway as a
`remote_mcp` tool with a fresh Cognito token per invocation; the Runtime uses
its normal `MCPClient`. Five measured sessions per arm after a warm-up. Design:
[`docs/HOSTING.md`](../docs/HOSTING.md).

## Prompt A/B

`just prompt-ab` creates two configuration-bundle versions (control/treatment
system prompt), starts an A/B test on the IAM front-door Gateway, sends 20
sessions through `/agent/invocations`, and reads `Correctness` and
`GoalSuccessRate` from the online evaluation config. The Runtime applies the
assigned prompt in a `BeforeModelCallEvent` hook. The script stops/deletes the
A/B test and deletes both bundles on exit. Same doc:
[`docs/HOSTING.md`](../docs/HOSTING.md).
