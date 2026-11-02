# AgentCore coverage

Status on 2026-11-02, after phases 0–7 and teardown. **Twelve of the thirteen
AgentCore services are in use; Payments is deliberately out of scope.** A row in
this table is a claim with a command or document behind it, not a feature
checklist.

Status key:

- **Measured** — deployed, exercised, and recorded in `RESULTS.md`.
- **Measured (partial)** — used in a smoke test or one scenario; the limit is
  named beside the row.
- **Not used** — no path exists, and this repo makes no claim about it.

## In use

| Service | Status | What this repo uses | Control it buys | Evidence | Limits |
|---|---|---|---|---|---|
| **Runtime** | Measured | Strands agent in an arm64 container; IAM `/invocations`; same Runtime used for normal and A/B traffic | A hosting model that keeps the agent boring and the tool surface external | `just eval`; `just hosting-compare`; `RESULTS.md` phases 1 and 6 | One region, one account. Platform cost is not measured; only Bedrock inference cost is in phase 6. |
| **Memory** | Measured | Actor + session ids; history read before model calls and written after | Multi-turn continuity without putting conversation state in the agent process | `just eval`; `RESULTS.md` Memory section; `agent/memory.py` | One run; the old reverse-order bug is fixed, but a later session still had an empty response. No concurrency or retention test. |
| **Gateway** | Measured | Tool Gateway with MCP, `CUSTOM_JWT`, policy and six targets across four target types; target deletion revokes a tool | One tool inventory, one auth boundary, one revocation point | `just tool-surface`; `just eval` (0 unmediated tool calls); `RESULTS.md` phase 4 | One region. Tool calls are smoke tests, not a load test. Gateway metrics are batched. |
| **Identity** | Measured | Cognito user pool; JWT at the Gateway; request interceptor; IAM-signed API Gateway target; `GetUser` in the MCP Lambda | The tool sees the caller identity, and tools can be withdrawn without redeploying the Runtime | `just identity-check`; `docs/IDENTITY.md` | Cognito only; no third-party IdP. The Runtime endpoint is IAM-only, so the token is carried in the payload rather than enforced on the Runtime. Tool revocation does not revoke already-issued access tokens. |
| **Observability** | Measured | ADOT/OTel spans for model and tool calls → CloudWatch Transaction Search; Gateway invocation/decision metrics | Failures are visible per turn, not inferred from a final answer | `just eval`; `RESULTS.md` phase 1; `docs/API-NOTES.md` | No SLO dashboard. Transaction Search is account-level. One region; no long-retention analysis. |
| **Evaluations** | Measured | Batch evaluation over spans: built-in `Correctness`, `GoalSuccessRate`, code evaluator `EchoFidelity`, `FailureAnalysis`; online evaluation for A/B | Scores that can fail a seeded violation and can be compared with another platform | `just eval`; `just phoenix-compare`; `RESULTS.md` phase 1; `docs/EVAL-COMPARISON.md` | 11 sessions / 26 turns. Judge token usage is not exposed. One judge model. The prebuilt correctness pair compares different prompts, so κ is not a pure platform comparison. |
| **Policy** | Measured | Cedar/Dogwood policy engine in `ENFORCE`: baseline permit, parameter quota, temporal CRM rule, natural-language-generated rule | Limits enforced outside the agent, including a second-call deny | `just policy-check`; `docs/POLICY.md`; `RESULTS.md` phase 3 | One caller and region. The policy session id is caller-supplied and resettable; this is a loop guard, not anti-abuse. One synthetic quota tool. |
| **Code Interpreter** | Measured (partial) | Behind the `toolbox` MCP Runtime; `execute_python` tool | Managed code execution without exposing the built-in directly | `just tool-surface`; `RESULTS.md` phase 4 | One smoke call. No long-running, filesystem, or untrusted-code test. Session terminated after the check. |
| **Browser** | Measured (partial) | Behind the `toolbox` MCP Runtime; `browse_page` tool | Managed navigation without direct browser credentials in the agent | `just tool-surface`; `RESULTS.md` phase 4 | One `https://example.com` page; no authenticated sites, downloads, or multi-step interaction. Session terminated after the check. |
| **Registry** | Measured | `AGENT` custom descriptor + `MCP` record; data-plane semantic search | Other consumers can discover the agent and its seven tools | `just registry-publish`; `just registry-check`; `docs/REGISTRY.md`; `RESULTS.md` phase 5 | `APPROVE_ALL` is a demo control. The tool list is a stack-maintained projection, not a live catalog read. Search is eventually consistent. |
| **Harness** | Measured (partial) | Config-defined managed agent; one `remote_mcp` tool pointing at the same Gateway | A second hosting model measured on the same task and tool surface | `just hosting-compare`; `docs/HOSTING.md`; `RESULTS.md` phase 6 | Preview. n=5. Memory disabled, so the Runtime comparison is not fully controlled. One task. |
| **Optimization** | Measured (partial) | Immutable configuration bundles, front-door Gateway, A/B test, online evaluation | A prompt experiment routed by the service rather than a code deploy | `just prompt-ab`; `docs/HOSTING.md`; `RESULTS.md` phase 6 | Preview. 19 of 20 sessions scored; the treatment means were higher but **not significant**. No prompt winner or service recommendation is claimed. |

## Not used

| Service | Status | Why | What would be required |
|---|---|---|---|
| **Payments** | Not used | The support-operations domain does not move money, and adding a payment rail would be a feature tour rather than a control. No payment endpoint, credential, or session exists. | A real payment use case, an instrument/processor, reconciliation, and a failure policy. Out of scope for this extension. |

## What the coverage does not mean

- “Measured” does not mean production-scale. The largest experiment is 26 turns;
  the hosting comparison is five sessions per arm.
- The tool boundary is the load-bearing claim: the phase 1 run recorded 21
  `execute_tool` spans, 20 through `echo-target___echo` and **0 direct**. The
  historical 2026-10-13 substring pass rate is not used as evidence for it.
- Cost is only partly known. Phase 6 reports **Bedrock inference cost** at list
  price; Runtime/Harness/Gateway/Memory platform charges remain unmeasured.
- Teardown is part of the claim: see the teardown section in `RESULTS.md` and
  run `just teardown-check`. The shared CDK bootstrap stack and asset stores are
  account-level infrastructure, not application resources.
