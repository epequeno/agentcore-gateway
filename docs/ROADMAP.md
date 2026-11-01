# Roadmap

What this repo is becoming, and what is next. Design and reasoning live in the
workspace spec note (`notes/2026-10-28-agentcore-reference-extension.md`); this
file is the repo's copy of the order of work. Where the two disagree, the note
wins and this file is wrong.

**Current phase: 0. Next action: 2026-11-16** — phases 0–1, about three
evenings and roughly $8 of an $80 cap.

## What exists

Runtime, Gateway and Memory are deployed, measured and torn down; the
measurements predate the current SDKs. Four of thirteen AgentCore services are
in use, plus one partial (Observability is CloudWatch logs only, no spans). The
two most valuable gaps are named in the repo already: **Identity**, and a real
evaluation instead of the substring check described in `RESULTS.md`.

## Phases

Each phase adds capability *and* a control, so the repo reads as a design rather
than a feature tour. A phase that exists only to "cover" a service gets cut.

| Phase | Adds | The control it buys | Services | Acceptance |
|---|---|---|---|---|
| **0. Re-baseline** | SDKs to current, AgentCore CLI v1, old numbers either re-measured or marked historical | Numbers that still reproduce | — | pins current in `pyproject.toml`/`uv.lock`; `just verify` green; every `RESULTS.md` row dated or marked historical at `fc9abe3` |
| **1. Observe and evaluate** | ADOT/OTel spans → CloudWatch Transaction Search → Observability; Evaluations with built-ins, one code-based Lambda evaluator and `FailureAnalysis`; the same spans scored in Arize Phoenix | An evaluation that survives "a no-tool run would also pass this", with portability across platforms | Observability, Evaluations | one session with spans for every model and tool call; a batch evaluation over the ten scripted sessions; the code evaluator fails a seeded violation on purpose; `FailureAnalysis` returns a grouped insight; `docs/EVAL-COMPARISON.md` reports agreement, κ, disagreements and cost per case for three evaluator pairs |
| **2. Identity** | JWT inbound authorizer (Cognito), outbound credential provider for a third-party API, Consent Portal | The agent acts **on behalf of a user**, and the user grants that | Identity, Gateway | an unauthenticated request is rejected at the Gateway; an authenticated token reaches the tool as a principal; the consent flow is captured; revocation stops access without a redeploy |
| **3. Policy** | Cedar/Dogwood engine on the Gateway: parameter rules, a temporal rule, one natural-language-authored policy | Deterministic limits enforced outside the agent's code | Policy | a policy denies an over-limit call and the agent surfaces a refusal, not an error; a temporal rule blocks the second action; every decision is in CloudWatch |
| **4. Tool surface** | OpenAPI target, Step Functions connector target, Code Interpreter and Browser as tools | Heterogeneous tools under one auth boundary and one policy engine | Gateway, Code Interpreter, Browser | one call through each target type, attributed correctly in Gateway records |
| **5. Registry** | agent and tools published as catalog entries, semantic search | Discovery and reuse across an org — the thing that makes it a platform | Registry | a fresh caller finds the agent by semantic search and the catalog matches the deployed Runtime |
| **6. Harness and Optimization** | the same task on Harness vs Runtime; two system prompts A/B'd through Gateway with eval scores as the metric | A measured answer to "which hosting model, which prompt" | Harness, Optimization (preview) | both result sets in `RESULTS.md` with latency and cost per session |
| **7. Ship** | `COVERAGE.md`, dated `RESULTS.md` rows, architecture diagram, teardown proof | The artefact a reviewer reads in five minutes | — | `cdk destroy` leaves nothing billing; no account ids, secrets or ARNs committed |

Eleven of thirteen services; Payments is out of scope for a support-operations
domain.

## Known preconditions for phases 0–1

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

## Open decisions

- **Faithfulness has nothing to be faithful to.** The only tool is `echo` and 20
  of 25 expected strings appear in their own prompt. Either add a retrieval-shaped
  tool plus grounded sessions, or cut that row and say phase 1 measured two pairs.
- **Judge model.** One Bedrock model on both sides, recorded next to the table.
  `README.md` records why two candidates are unavailable in this account.
- **Region.** Everything is `us-east-1`. Registry GA lives in `us-east-2`,
  `eu-central-1`, `eu-west-2` and `ca-central-1`, and the public-preview
  namespace was discontinued 2026-10-30 — so phase 5 constrains the region, and
  phase 0 is the cheap moment to notice it.

## Sequencing with the durable-eval project

Phases 0–1 land first: the durable project's experiment depends on trace-based
evaluation working, and the fastest way to learn its edges is on the repo that
already has a deployed agent and ten scripted sessions. Then durable phases 0–3,
then phases 2–7 here.

## Budget

$80 for the whole extension, separate from the durable project's cap. Runtime
idle time and evaluation tokens are the expensive parts; tear down after each
measurement session. The Phoenix comparison roughly doubles phase 1's judge
spend — ten sessions across three evaluator pairs is 60 judge calls, not 600.

## Risks that change the order

- **Identity is the hardest phase** and the most likely to eat a week. If it
  stalls, ship 3–5 first; Identity is the phase to move, never to fake.
- **Optimization is preview.** A stretch, never the load-bearing claim.
- **The old numbers go stale.** They were measured at `fc9abe3` on 2026-10-13;
  re-measure after the SDK upgrade or label them historical, never leave an
  undated claim standing.
