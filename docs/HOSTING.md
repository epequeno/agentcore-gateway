# Harness vs Runtime, and a prompt A/B

Phase 6 asks two questions that are easy to answer with adjectives and hard to
answer with data:

1. **Which hosting model?** The same agent task on AgentCore **Harness** and
   AgentCore **Runtime**.
2. **Which system prompt?** Two prompts, same model and tools, A/B-tested
   through a Gateway front door and scored by online evaluation.

Both experiments are small on purpose. Results are a case study, not a
benchmark.

## Hosting comparison

The two arms share the model (`deepseek.v3.2`), the baseline system prompt, the
same seven-tool Gateway catalog, and the same task prompt. The Runtime reaches
the Gateway through `MCPClient` with the caller's Cognito token. The Harness is
a config-defined managed agent; at invoke time it receives one `remote_mcp`
tool pointing at the same Gateway `/mcp` endpoint with a fresh token in the
`Authorization` header. Nothing about the tool surface is imported into either
arm.

```sh
just hosting-compare
```

The script warms both arms, then measures five sessions per arm. Each row
records wall time, model token usage, and an estimate at the Bedrock
**Standard on-demand list price** for DeepSeek v3.2 in `us-east-1`:
$0.62 per 1M input tokens and $1.85 per 1M output tokens, read from the
Bedrock pricing page on 2026-11-02. Platform charges for Runtime, Harness,
Gateway, Memory and logs are **not** included; this is inference cost.

The task is one CRM lookup through the Gateway, with an exact expected account
id and a fixed closing phrase. A run passes only if both appear.

### Measured 2026-11-02

| Arm | n | Pass | p50 wall (ms) | Mean wall (ms) | Input tokens | Output tokens | Inference cost / session |
|---|---:|---:|---:|---:|---:|---:|---:|
| Runtime | 5 | 5/5 | 7,663 | 6,825 | 9,023 total | 389 total | $0.001263 |
| Harness | 5 | 5/5 | 6,286 | 7,458 | 6,805 total | 105 total | $0.000883 |

Harness used **30% less inference money per session** and had the lower p50;
Runtime had the lower mean because one harness session took 12.8 s. With n=5
this is not a distribution and the means should not be read as a winner. The
honest reading is that both arms completed the task, and the cost difference
came from output tokens, not from a different number of tool calls.

The Runtime arm still performs its normal AgentCore Memory lookup and write,
while the Harness arm has memory disabled. Memory was not exercised by this
single-turn task, but that is a platform difference, not a controlled variable.

## Prompt A/B

A prompt-only change should not require a code deployment. The treatment is an
immutable **configuration bundle** version; both variants run on the same
Runtime.

```text
client ──▶ AgentCore front Gateway (AWS_IAM)
                 │  assigns a bundle variant by session id
                 ▼
           Runtime target: /agent/invocations
                 │
                 ├─ config bundle ref in W3C baggage
                 ├─ BeforeModelCallEvent hook applies system_prompt
                 └─ tools still arrive only from the tool Gateway
```

The front door is a second Gateway. The original Gateway remains MCP-only with
its Cognito JWT authorizer and policy engine; the front door has one HTTP
Runtime target and is used only for A/B routing.

### What is compared

- **Control:** the deployed baseline prompt.
- **Treatment:** the same prompt plus an explicit tool-use and tool-reporting
  policy.
- **Constant:** model, tools, Runtime, Gateway, online evaluators.
- **Reward signal:** `Builtin.Correctness` and `Builtin.GoalSuccessRate`,
  created as one online evaluation config with 100% sampling and a two-minute
  session timeout.

`just prompt-ab` creates the two bundles, creates and starts the A/B test,
sends 20 unique sessions through the front door, waits for evaluator results,
then stops/deletes the test and deletes both bundles. The raw responses and
service result are written to the gitignored `results/optimization.json`.

### Measured 2026-11-02

Twenty sessions were sent; the service scored 19 (12 control, 7 treatment).
All twenty HTTP requests completed without an error.

| Evaluator | Control mean (n=12) | Treatment mean (n=7) | Delta | p | Significant |
|---|---:|---:|---:|---:|---|
| `Builtin.Correctness` | 0.958 | 1.000 | +4.3% | 0.774 | no |
| `Builtin.GoalSuccessRate` | 0.917 | 1.000 | +9.1% | 0.742 | no |

The treatment has the higher mean on both evaluators, but neither result is
statistically significant, and one session was not scored. The honest verdict
is **no measured winner**. This is a routing and evaluation demonstration, not
evidence that the hand-written treatment is a better prompt.

### Design limits

- The A/B service assigns a variant per session and is sticky for that session.
  The experiment therefore compares sessions, not individual turns.
- Online evaluation reads Runtime spans, so the result appears only after a
  session is complete and the evaluator has run. The script waits up to 20
  minutes.
- Config-bundle routing depends on the front-door Gateway and the
  `BeforeModelCallEvent` hook. Calling the Runtime directly bypasses both and
  uses the deployed default prompt.
- The treatment is hand-written. It is not a service-generated recommendation;
  the A/B service is the Optimization capability exercised here.

## Teardown

The front door, its Runtime target, the online-evaluation config, its role, the
A/B execution role and the Harness are CloudFormation resources and disappear
with `just down`. The A/B test and configuration bundles are service resources
created by `just prompt-ab`; the script deletes them in a `finally` block, and
`just down` calls `scripts/cleanup_phase6.py` first in case a run was killed.

Teardown was verified after this measurement: no stack, A/B test, bundle,
online-evaluation config, Harness, Memory, Policy engine or Registry remained;
the phase-6 log groups were deleted and all Browser/Code Interpreter sessions
were `TERMINATED`.
