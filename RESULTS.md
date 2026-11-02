# Results

Every number here is produced by a command in this repo and dated. A row with no
date has not been measured — and a number without its command is not evidence.

**Historical: measured at commit `fc9abe3`**, the tree that was deployed, on
2026-10-13. Later commits change the repo, not these numbers.

> **Re-baselined 2026-11-01.** The dependencies moved to
> `bedrock-agentcore` 1.25.2, `strands-agents` 1.63.0 and `aws-cdk-lib`
> 2.275.0. Everything in the table below was measured against 1.24.1 / 1.60.0 /
> 2.240.0 and is **not** a claim about the current tree. Phase 1 replaces the
> substring pass rate with span-based evaluation; new rows are dated separately.

## Headline — historical (2026-10-13, `fc9abe3`)

| Metric | Value | Command | n | Date |
|---|---|---|---|---|
| Eval pass rate, 10 scripted sessions | 100% (10/10 sessions, 25/25 turns) | `just eval` | 10 | 2026-10-13 (historical) |
| Cold start (ms) | 14,572 | `just eval` | 1 | 2026-10-13 (historical) |
| Warm p50 invocation (ms) | 3,655 | `just eval` | 24 | 2026-10-13 (historical) |
| Cost per session (USD) | not measured — see AWS Cost Explorer after teardown | — | 0 | not measured |
| Tool calls routed via Gateway | all (confirmed via CloudWatch logs, by hand) | `aws logs tail` | — | 2026-10-13 (historical) |

## The claim these rows are meant to support

That the tool surface lives in Gateway rather than in the agent: every tool call
in the eval must appear in the Gateway's invocation records, and **zero** tool
calls should reach a target directly. The last row is the one that tests it.

## Phase 1 — observed and evaluated (2026-11-01, current tree)

Measured on the current tree with `bedrock-agentcore` 1.25.2 and adot 0.22.0.

| Metric | Value | Command | n | Date |
|---|---|---|---|---|---|
| Spans for every model and tool call | `invoke_agent`, `chat`, `execute_tool`, `mcp tools/call` present, each carrying `session.id` | `just eval` | 26 turns | 2026-11-01 |
| Batch evaluation over scripted sessions | COMPLETED: 11/11 sessions, 26 turns | `just eval` | 11 | 2026-11-01 |
| Built-in correctness | 92.3% (24/26 turns), 2 incorrect: s04 turn 3 (empty response), s06 turn 2 (claimed the tool was unavailable) | `just eval` | 26 | 2026-11-01 |
| Goal success rate | 81.8% (9/11 sessions) — the two failures are the seeded violation and s04 | `just eval` | 11 | 2026-11-01 |
| Code evaluator (`EchoFidelity`) | 81.8% (9/11 PASS). Fails the seeded `s11` as designed, and s04's empty response. **Control works** | `just eval` | 11 | 2026-11-01 |
| `FailureAnalysis` | 2 grouped insights over 5 affected sessions: tool-availability hallucination, and malformed/empty output extraction | `just eval` | 11 | 2026-11-01 |
| Tool-call attribution | 21 `execute_tool` spans: 20 `echo-target___echo` + 1 `x_amz_bedrock_agentcore_search`, **0 direct** | `just eval` | 21 | 2026-11-01 |
| Cold start (fresh deploy, 1 invoke) | 15,320 ms | `invoke_agent_runtime` probe | 1 | 2026-11-01 |
| Warm p50 invocation | 3,795 ms | `just eval` | 26 | 2026-11-01 |
| Phoenix vs AgentCore, prebuilt correctness | 73% agreement, κ 0.117 (7 disagreements, all context/prompt differences) | `just phoenix-compare` | 26 | 2026-11-01 |
| Phoenix vs AgentCore, custom rubric | 100% agreement, κ 1.000 (0 disagreements) | `just phoenix-compare` | 26 | 2026-11-01 |
| Phoenix judge cost per turn | $0.00048 (52 calls, $0.0125 total; AgentCore judge usage is not exposed) | `just phoenix-compare` | 26 | 2026-11-01 |

## Phase 3 — policy (2026-11-02, current tree)

Measured with the policy engine in `ENFORCE` mode. The boundary, refusal and
CloudWatch rows come from `just policy-check`; the natural-language row records
the control-plane generation that produced the committed rule.

| Metric | Value | Command | n | Date |
|---|---|---|---|---|---|
| Parameter rule boundary | `amount=100` allowed, `amount=101` denied | `just policy-check` | 2 | 2026-11-02 |
| Temporal rule | first CRM lookup allowed; second in the same policy session denied | `just policy-check` | 2 | 2026-11-02 |
| Agent-facing refusal | "Policy refused the action ... blocked by the policy quota system" | `just policy-check` | 1 | 2026-11-02 |
| CloudWatch `DenyDecisions` | `quota_limit` 2, `crm_once_per_session` 1 (30-minute window; includes boundary probes) | `just policy-check` | 3 | 2026-11-02 |
| CloudWatch `AllowDecisions` | `gateway_baseline_permit` 33 (same window, includes phase 4 tool calls) | `just policy-check` | 33 | 2026-11-02 |
| Natural-language policy | `StartPolicyGeneration` produced `context.input.amount > 100`; no findings | control-plane API | 1 | 2026-11-02 |

The denial is HTTP 200 with JSON-RPC error `-32002`, so the row above tests the
agent's translation of a policy refusal, not an HTTP status. The stack was torn
down after this run; the policy engine and the agent's log group were deleted,
and no AgentCore resource is left billing. Design and limitations are in
[docs/POLICY.md](docs/POLICY.md).

## Phase 4 - tool surface (2026-11-02, current tree)

Measured with `just tool-surface` after `just deploy`. Every row is produced by
that command unless noted.

| Metric | Value | Command | n | Date |
|---|---|---|---|---|
| Gateway catalog | 7 tools: `echo-target`, `crm-mcp`, `openapi-status`, `workflow`, `toolbox` (2), `policy-quota` | `just tool-surface` | 7 | 2026-11-02 |
| Lambda target | `echo-target___echo` returned `echo-target-ok` | `just tool-surface` | 1 | 2026-11-02 |
| MCP server target | `crm-mcp___getAccount` returned `ACME-42` and the token subject | `just tool-surface` | 1 | 2026-11-02 |
| OpenAPI target | `openapi-status___fetchStatus` returned `openapi-target-ok`; wrong key returns 401 | `just tool-surface` | 1 | 2026-11-02 |
| Step Functions connector | `workflow___StartExecution` returned an execution ARN | `just tool-surface` | 1 | 2026-11-02 |
| Code Interpreter tool | `toolbox___execute_python` printed and returned `toolbox-ok` | `just tool-surface` | 1 | 2026-11-02 |
| Browser tool | `toolbox___browse_page` loaded `https://example.com` and returned title `Example Domain` | `just tool-surface` | 1 | 2026-11-02 |
| Gateway target-type attribution | `AWS/Bedrock-AgentCore` `Invocations` with `TargetType=CONNECTOR` and `TargetType=LAMBDA`; per-tool `Name` dimensions include `workflow`, `openapi-status`, and `toolbox` | `just tool-surface` | - | 2026-11-02 |

The gateway catalog prefixes every tool with its target name. A direct target
call cannot produce these names. The policy engine remains attached and its
baseline permit covers the new tools; no target-specific rule was added in this
phase. The stack was destroyed after the run; no Runtime, Gateway, policy engine,
state machine, Function URL, or built-in-tool session remains. All five Browser
and seven Code Interpreter sessions created by the checks are `TERMINATED`;
CloudWatch log groups were deleted. Design and limits are in
[docs/TOOL-SURFACE.md](docs/TOOL-SURFACE.md).

## Phase 5 — registry (2026-11-02, current tree)

Measured after `just deploy` and `just registry-publish`; no ARNs are copied
into this file.

| Metric | Value | Command | n | Date |
|---|---|---|---|---|
| Catalog records published | `agentcore-gateway-agent` (`AGENT`) and `agentcore-gateway-tools` (`MCP`), both `APPROVED` | `just registry-publish` | 2 | 2026-11-02 |
| Fresh semantic search for the agent | query about support/billing with CRM and workflow tools returned `agentcore-gateway-agent` as `AGENT`; exact record name was not in the query | `just registry-check` | 1 | 2026-11-02 |
| Catalog matches deployed Runtime | agent descriptor `runtimeArn` equals `AGENTCORE_RUNTIME_ARN` | `just registry-check` | 1 | 2026-11-02 |
| Tool catalog discovered | `agentcore-gateway-tools` returned as `MCP` with 7/7 Gateway tools | `just registry-check` | 7 | 2026-11-02 |

Registry search is eventually consistent: approval is immediate in the control
plane, while the discovery index can lag. The check retries for up to five
minutes. The tools payload is a projection maintained in the stack, so a target
change needs a redeploy; the Runtime ARN does not. See
[docs/REGISTRY.md](docs/REGISTRY.md).

The stack was destroyed after the run. The registry and both records are gone;
CloudFormation reports no stack, and `ListMemories`, `ListPolicyEngines` and the
workflow listing are empty. All Browser and Code Interpreter sessions created by
the checks are `TERMINATED`; CloudWatch log groups were deleted.

## Phase 6 — Harness vs Runtime (2026-11-02, current tree)

The task was one CRM lookup through the Gateway, with the same model
(`deepseek.v3.2`), baseline system prompt and seven-tool catalog on both arms.
Both arms were warmed before the five measured sessions. Wall time is
client-observed around the invocation; cost is **Bedrock inference only** at
the published Standard on-demand list price for DeepSeek v3.2 in `us-east-1`
($0.62 / 1M input, $1.85 / 1M output). Platform charges are not included.

| Arm | n | Pass | p50 wall (ms) | Mean wall (ms) | Input tokens | Output tokens | Inference cost / session |
|---|---:|---:|---:|---:|---:|---:|---:|
| Runtime | 5 | 5/5 | 7,663 | 6,825 | 9,023 total | 389 total | $0.001263 |
| Harness | 5 | 5/5 | 6,286 | 7,458 | 6,805 total | 105 total | $0.000883 |

Harness used 30% less inference cost and had the lower p50; Runtime had the
lower mean because one Harness session took 12.8 s. n=5 is a case study, not a
distribution. The Runtime arm also performs its normal Memory lookup/write; the
Harness arm has memory disabled. Design and limits:
[docs/HOSTING.md](docs/HOSTING.md).

## Phase 6 — prompt A/B (2026-11-02, current tree)

Twenty sessions were sent through the Gateway runtime target with a control or
treatment bundle. The service scored 19 of them (12 control, 7 treatment); all
20 HTTP requests returned without an error. Results are means on a 0–1 scale;
a two-sided test with this sample size is not powered to detect small
differences.

| Metric | Control (n=12) | Treatment (n=7) | Delta | p | Significant |
|---|---:|---:|---:|---:|---|
| Builtin.Correctness | 0.958 | 1.000 | +4.3% | 0.774 | no |
| Builtin.GoalSuccessRate | 0.917 | 1.000 | +9.1% | 0.742 | no |

The treatment was better on both means, but neither difference is statistically
significant and one session was not scored. The honest conclusion is **no
measured winner**; the experiment demonstrates the routing and scoring path,
not a prompt improvement. The control and treatment differ only in
`system_prompt`; model, Runtime, Gateway, tools and evaluators are shared.

The stack was destroyed after the run. No A/B test, configuration bundle,
online-evaluation config, Harness, Memory, Policy engine or Registry remains;
the four phase-6 CloudWatch log groups were deleted. The Browser and Code
Interpreter sessions created by the checks are all `TERMINATED`.

## Memory continuity

> **2026-11-01: this section caught a real bug.** The old harness scored s04 and
> s05 as passing. Evaluation showed that `get_last_k_turns` returns newest-first,
> and the agent passed that list to the model unchanged — so the history was
> reversed and s04 answered "turn two" when asked for the first thing it echoed.
> `agent/memory.py` now reverses the turns; s05 passes after the fix. s04 still
> returns an empty third-turn response, which is a separate model/extraction
> failure (see `FailureAnalysis`), not a memory-order failure.

The 2026-10-13 observations, kept for the record:

- **s03 turn 2**: "What did I ask you to echo in my first message?" → agent recalls
  "first message" from turn 1. ✅
- **s05 turn 2**: "What did I ask you to echo in turn 1?" → agent recalls
  "remember this fact". ✅
- **s05 turn 4**: "What did I ask you to echo in turn 3?" → agent recalls
  "still works". ✅

## No-tool baseline

- **s09 turn 2**: "What is 2+2?" without asking for a tool → agent answers "4"
  without inventing a tool call. ✅
- **s10 turn 1**: "What is the capital of France?" without a tool → agent answers
  "Paris" without calling echo. ✅

## Limitations

Read this before quoting anything above.

- **The pass rate does not test the thesis.** `_check_pass` is a case-insensitive
  substring test, and 20 of the 25 expected strings appear verbatim inside their
  own prompt — so a run with **no tool access at all** would also score 100%. The
  no-tool baseline below shows the model answering without tools. The harness
  records `tool_calls_via_gateway` as *not measured*, and the row above was
  confirmed by hand with `aws logs tail`, not by `just eval`. Phase 1 later
  automated this for the current tree: 21 `execute_tool` spans, 20 via
  `echo-target___echo` and 0 direct. This paragraph describes the historical
  table only.
- **Total cost per session is unmeasured.** Phase 6 reports Bedrock inference
  cost at list price only; Runtime, Harness, Gateway, Memory and log platform
  charges are not included. The stack has been torn down, so a total needs AWS
  Cost Explorer or a redeploy.
- **Policy evidence is one caller and one region.** The quota rule covers one
  synthetic tool; temporal history is caller-supplied and resettable. See
  [docs/POLICY.md](docs/POLICY.md).
- **One deployment, one run.** 10 sessions and 25 turns is a smoke test, not a
  distribution; the latency figures come from 24 warm invocations.
- Cold start (14.6 s) is reported separately from warm latency rather than averaged
  in, because container start dominates it and averaging would hide it.

## Identity — 2026-11-01

| Check | Result | Command | n | Date |
|---|---|---|---|---|
| Unauthenticated Gateway `tools/list` | **HTTP 401**, missing bearer token | `just identity-check` | 1 | 2026-11-01 |
| Authenticated Gateway catalog | 2 tools: `crm-mcp___getAccount`, `echo-target___echo` | `just identity-check` | 1 | 2026-11-01 |
| Principal at the tool | token `sub` returned as `principal`; `support-user@example.com`; `ACME-42` | `just identity-check` | 1 | 2026-11-01 |
| Revocation without Runtime redeploy | deleting the `crm-mcp` target removed it from the catalog | `just identity-check --revoke` | 1 | 2026-11-01 |
| Target auth | API Gateway `AWS_IAM`; only the Gateway role has `execute-api:Invoke` | synthesized policy / deployed route | - | 2026-11-01 |
| Token validation at the tool | Cognito `GetUser` on `x-agentcore-user-token` | MCP Lambda | 1 | 2026-11-01 |

The principal assertion compares the access token's `sub` with the tool
response; it does not rely on a username substring. The revocation row says the
tool was removed, not that the user's token was revoked: Cognito access tokens
remain valid until expiry.

## Identity — rejected paths, measured the same day

These are recorded so they are not retried:

| Path | Result |
|---|---|
| Hosted consent portal + Cognito | `SignInRejected` after Cognito login |
| Pre-portal 3LO + local BFF | grant stored; Gateway's service-linked identity could not see it |
| `JWT_PASSTHROUGH`, OpenAPI target | target type rejects it |
| `JWT_PASSTHROUGH`, MCP server target | target type rejects it |
| OAuth MCP target | discovery needs a token; catalog had only the echo tool |

The working design is the Gateway request interceptor: it forwards the
already-validated Cognito token in an allowlisted header to an IAM-only target;
see [docs/IDENTITY.md](docs/IDENTITY.md).

## Phase 7 — ship evidence (2026-11-02)

The ship gate is reproducible, not a prose claim. `just verify` covers lint,
tests, and synthesis; `just teardown-check` queries the deployed-service APIs;
`just secret-scan` reads the working tree, including untracked files that are
not ignored.

| Check | Result | Command |
|---|---|---|
| Coverage matrix | 12 of 13 services used; Payments explicitly out of scope | [COVERAGE.md](COVERAGE.md) |
| Architecture | Request path, both Gateways, control plane, and exclusions | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Lint, tests, synth | 24 tests passed; Ruff clean; CDK synth clean | `just verify` |
| Application stack | `AgentCoreGatewayStack` absent | `just teardown-check` |
| AgentCore resources | 0 runtimes, gateways, memories, policy engines, harnesses, online-eval configs, configuration bundles, A/B tests, registries | `just teardown-check` |
| App log groups | 0 matching `/aws/lambda/AgentCoreGatewayStack-*` or app AgentCore log groups | `just teardown-check` |
| Built-in sessions | 18 created, 0 active; all `TERMINATED` | `just teardown-check` |
| Tracked/untracked file scan | no real account IDs, deployed ARNs, or keys | `just secret-scan` |
| Retained bootstrap | 1 ECR asset repository (29 image entries) and 2 S3 asset buckets; account-level CDK bootstrap, not app resources | `just teardown-check` |

The scan allows two things that are not deployed resources: wildcard IAM
resource patterns such as `arn:aws:logs:*:*:log-group:...` in the stack, which
contain no account id, and the fake evaluator ARN in a unit test with account
`123`. Neither names an account resource; a real ARN and a real account id are
rejected.

CloudFormation destroyed the stack, but it did not delete CloudWatch log groups
for the Lambdas or AgentCore runtime/evaluation logs. `just down` now runs
`scripts/cleanup_phase7.py` after destruction; that removed 8 Lambda and 4
AgentCore log groups in this run. The teardown check then reported zero.

The retained CDK bootstrap stack (`CDKToolkit`) and its S3/ECR asset stores are
shared account-level infrastructure. They were not created by this application
and are intentionally not deleted; deleting them would break later deploys and
other CDK applications. No **application** resource remains billing.

## Teardown — phase 1 (2026-11-01)

`just down` destroyed `AgentCoreGatewayStack` (Runtime, Memory, Gateway, tool
and CRM Lambdas, Cognito pool/domain, both evaluators, and the interceptor
Lambda). `list-agent-runtimes`, `list-gateways` and `list-memories` return
empty; the runtime, Lambda and batch-evaluation log groups were deleted after
the rows above were recorded. Nothing from this run is still billing.

## How to reproduce

```sh
export AWS_PROFILE=arch
just deploy
export AGENTCORE_RUNTIME_ARN=<from deploy outputs>
export AGENTCORE_GATEWAY_URL=<from deploy outputs>
export AGENTCORE_MEMORY_ID=<from deploy outputs>
just eval
just down        # do this — an idle Runtime still bills
just teardown-check
just secret-scan
```

Record the commit SHA, the date, and the exact command beside every number.
`just ship-check` runs the final gate: `verify`, `teardown-check`, and
`secret-scan`.
