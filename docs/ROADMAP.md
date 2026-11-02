# Roadmap

What this repo is becoming, and what is next. Design and reasoning live in the
workspace spec note (`notes/2026-10-28-agentcore-reference-extension.md`); this
file is the repo's copy of the order of work. Where the two disagree, the note
wins and this file is wrong.

**Phase 3 (Policy) is met.** A Gateway policy engine runs in `ENFORCE` mode with
a natural-language-authored parameter rule (`amount > 100`), a Dogwood temporal
rule (one CRM lookup per policy session), and a baseline permit. The agent
surfaces a policy refusal rather than a tool error; CloudWatch records allow and
deny decisions. See [POLICY.md](POLICY.md).

**Phase 4 (Tool surface) is met.** The Gateway lists seven tools across six
targets: Lambda, two MCP servers, OpenAPI, and the Step Functions connector.
Code Interpreter and Browser sit behind the same MCP server target; one call
through every target passed and Gateway metrics name the tools and target types.
See [TOOL-SURFACE.md](TOOL-SURFACE.md).

**Phase 5 (Registry) is met.** The Runtime is published as an `AGENT` custom
record and the Gateway as an `MCP` record; a fresh data-plane caller finds the
agent by natural-language search and its descriptor's Runtime ARN matches the
deployment. See [REGISTRY.md](REGISTRY.md).

**Phase 6 (Harness and Optimization) is met.** Harness and Runtime completed the
same Gateway-backed CRM task with wall time, tokens and inference cost recorded;
a config-bundle A/B test routed two system prompts through a separate IAM front
Gateway and scored 19 sessions with `Correctness` and `GoalSuccessRate`. The
treatment means were higher, but neither difference was significant, so the
verdict is no measured winner. See [HOSTING.md](HOSTING.md) and `RESULTS.md`.

**Phase 7 (Ship) is met.** `COVERAGE.md` records the 13-service matrix,
`docs/ARCHITECTURE.md` shows both request paths, `RESULTS.md` carries the dated
evidence and teardown audit, and `just teardown-check` plus `just secret-scan`
make the ship gate reproducible. The only retained resources are the shared CDK
bootstrap stack and its asset stores, which are account-level infrastructure and
are reported, not hidden.

Phase 0 landed 2026-11-01: `bedrock-agentcore`
1.25.2, `strands-agents` 1.63.0, `aws-cdk-lib` 2.275.0, AgentCore CLI 1.0.1, and
the 2026-10-13 numbers marked historical. Phase 1 also landed the same day:
ADOT spans in the agent's log group, batch evaluation over 11 sessions, a
deterministic evaluator that fails a seeded violation, two `FailureAnalysis`
insights, automated tool attribution, and a Phoenix comparison. The comparison
reports **two** evaluator pairs, not three: faithfulness was cut because `echo`
retrieves nothing. Phases 0–1 stayed inside the $8 estimate. Phase 2 landed the
same day: Cognito principal-at-tool and target-deletion revocation, no portal or
third-party IdP.

## What exists

Runtime, Gateway and Memory are deployed, measured and torn down. Twelve of the
thirteen AgentCore services are now in use: Runtime, Memory, Gateway, Identity,
Observability (spans, not just logs), Evaluations, Policy, Code Interpreter,
Browser, Registry, Harness and Optimization (prompt A/B). The two most valuable
gaps named in the repo were **Identity** and a real evaluation; both closed on
2026-11-01. Policy, the heterogeneous tool surface, Registry and phase 6 closed
on 2026-11-02. Phase 7 closed the evidence and teardown record on 2026-11-02.
Payments is out of scope.

## Phases

Each phase adds capability *and* a control, so the repo reads as a design rather
than a feature tour. A phase that exists only to "cover" a service gets cut.

| Phase | Adds | The control it buys | Services | Acceptance |
|---|---|---|---|---|
| **0. Re-baseline** | SDKs to current, AgentCore CLI v1, old numbers either re-measured or marked historical | Numbers that still reproduce | — | pins current in `pyproject.toml`/`uv.lock`; `just verify` green; every `RESULTS.md` row dated or marked historical at `fc9abe3` |
| **1. Observe and evaluate** | ADOT/OTel spans → CloudWatch Transaction Search → Observability; Evaluations with built-ins, one code-based Lambda evaluator and `FailureAnalysis`; the same spans scored in Arize Phoenix | An evaluation that survives "a no-tool run would also pass this", with portability across platforms | Observability, Evaluations | **met 2026-11-01, with one documented deviation**: every model/tool call has spans; batch evaluation over 11 sessions; the code evaluator fails a seeded violation; `FailureAnalysis` returns grouped insights; `EVAL-COMPARISON.md` reports two pairs, not three — faithfulness was cut because `echo` retrieves nothing |
| **2. Identity** | JWT inbound authorizer (Cognito), request interceptor, IAM-only MCP target | The agent acts **on behalf of a user**, and the tool sees that user | Identity, Gateway | **met 2026-11-01 on three of four criteria**: 401 unauthenticated; token `sub` returned as principal; target deletion removes the tool without a Runtime redeploy. The fourth — a hosted consent portal flow captured as a trace — is **not delivered**: the portal rejects Cognito sign-in with `SignInRejected` and is recorded as a rejected path in `RESULTS.md`, not passed. See [IDENTITY.md](IDENTITY.md) |
| **3. Policy** | Cedar/Dogwood engine on the Gateway: parameter rules, a temporal rule, one natural-language-authored policy | Deterministic limits enforced outside the agent's code | Policy | **met 2026-11-02**: `amount=101` denied (100 allowed); the agent reports the policy refusal; the second CRM call in a policy session is denied; CloudWatch `AllowDecisions`/`DenyDecisions` are recorded. See [POLICY.md](POLICY.md) |
| **4. Tool surface** | OpenAPI target, Step Functions connector target, Code Interpreter and Browser as tools | Heterogeneous tools under one auth boundary and one policy engine | Gateway, Code Interpreter, Browser | **met 2026-11-02**: seven tools across six targets; one call each passed; Code Interpreter and Browser stay behind an MCP server target; Gateway metrics name the tools and target types. See [TOOL-SURFACE.md](TOOL-SURFACE.md) |
| **5. Registry** | agent and tools published as catalog entries, semantic search | Discovery and reuse across an org — the thing that makes it a platform | Registry | **met 2026-11-02**: `agentcore-gateway-agent` (`AGENT`, custom descriptor) and `agentcore-gateway-tools` (`MCP`, seven tools) are APPROVED; a fresh data-plane caller finds the agent by natural-language search and its descriptor's runtime ARN equals `AGENTCORE_RUNTIME_ARN`. See [REGISTRY.md](REGISTRY.md) |
| **6. Harness and Optimization** | the same task on Harness vs Runtime; two system prompts A/B'd through Gateway with eval scores as the metric | A measured answer to "which hosting model, which prompt" | Harness, Optimization (preview) | **met 2026-11-02**: both hosting arms passed 5/5 with latency and inference cost per session; the A/B test scored 19 sessions and found no significant prompt win. See [HOSTING.md](HOSTING.md) and `RESULTS.md` |
| **7. Ship** | `COVERAGE.md`, dated `RESULTS.md` rows, architecture diagram, teardown proof | The artefact a reviewer reads in five minutes | — | **met 2026-11-02**: `just verify` is green; `just teardown-check` reports no app resources or app log groups; no real account IDs, deployed ARNs or keys in tracked files. The shared CDK bootstrap stack/asset stores are retained by design and named in `RESULTS.md` |

Twelve of thirteen services; Payments is out of scope for a support-operations
domain.

## Known preconditions for phases 0–1

**Historical — written before the work, and every item below was resolved on 2026-11-01.** Kept because it records what was unknown at the start; read it as a pre-flight list, not as current state.

Recorded 2026-11-01, after reading the tree rather than the plan:

- **AgentCore CLI v1 is not installed** — `command -v agentcore` finds nothing.
- **No tracing in the agent.** `strands.telemetry.StrandsTelemetry` exists and
  takes a custom provider, so this is one provider with two span processors
  (CloudWatch + a local collector) plus `strands-agents[otel]` and
  `openinference-instrumentation-strands-agents`. The runtime role also needs
  trace permissions it does not have.
- **`StartBatchEvaluation` reads CloudWatch Logs**, not an inline session list.
  The runner is spans into a log group → start batch → poll.
- **The custom rubric is a control-plane resource** (`evaluatorConfig.llmAsAJudge`
  with `instructions`, `modelConfig`, `ratingScale`); the same instructions run
  through Phoenix's custom evaluator is the controlled comparison.
- **Cost per case does not exist yet.** Tokens per case come from spans; the
  price has to be recorded beside it.

## Open decisions — all three closed on 2026-11-01/02

- **Faithfulness has nothing to be faithful to — resolved by cutting the row.** The only tool is `echo` and 20 of 25 expected strings appear in their own prompt. Phase 1 reports two pairs, not three, and `docs/EVAL-COMPARISON.md` says why. The alternative (add a retrieval-shaped tool and grounded sessions) is a different project.
- **Judge model — resolved.** One Bedrock model (`deepseek.v3.2`) on both sides, recorded next to the table. `README.md` records why two candidates are unavailable in this account.
- **Region — resolved, and the old claim was wrong.** On 2026-11-02 the `agent-registry` control and data-plane endpoints worked in `us-east-1` and the phase 5 deploy succeeded there. The earlier "GA is us-east-2 only" note is stale and is not a constraint.

## Sequencing with the durable-eval project

**Superseded 2026-11-02.** The plan was phases 0–1 here, then durable phases 0–3, then phases 2–7. Phases 0–7 landed in one run, so the dependency the durable project was waiting on now exists: span-based evaluation, a code evaluator that can fail, and a Phoenix comparison are all in this repo. The durable project should consume that instrumentation rather than rebuild it, and its own Phases 0–3 are what remains of the original order.

## Budget

$80 for the whole extension, separate from the durable project's cap. Runtime
idle time and evaluation tokens are the expensive parts; tear down after each
measurement session. The Phoenix comparison roughly doubles phase 1's judge
spend. As built it was 11 sessions / 26 turns across **two** evaluator pairs —
52 Phoenix judge calls, $0.0125 total — not the 60 calls the plan estimated for
three pairs.

## Risks that change the order

- **Identity is the hardest phase** and the most likely to eat a week. If it
  stalls, ship 3–5 first; Identity is the phase to move, never to fake.
- **Optimization is preview.** A stretch, never the load-bearing claim.
- **The old numbers go stale.** They were measured at `fc9abe3` on 2026-10-13;
  re-measure after the SDK upgrade or label them historical, never leave an
  undated claim standing.
