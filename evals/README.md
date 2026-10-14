# evals — the headline number

**Implemented.** `just eval` runs ten scripted multi-turn sessions against a
deployed Runtime, measuring pass rate, cold-start, and warm latency. Set the
env vars from `just deploy` outputs first:

```sh
export AGENTCORE_RUNTIME_ARN=<arn>
export AGENTCORE_GATEWAY_URL=<url>
export AGENTCORE_MEMORY_ID=<id>
just eval
```

## The claim being tested

That the tool surface lives in **Gateway**, not in the agent. Two halves:

1. The ten scripted multi-turn sessions produce the expected outcome at a
   measurable pass rate.
2. Every tool call in those sessions appears in the Gateway's records, and
   **zero** tool calls reach a target directly.

Half two is the one that tests the thesis. A pass rate can improve for reasons
unrelated to where the tools live; only call attribution distinguishes the
mechanism from a coincidence.

## Sessions

The sessions in `sessions.jsonl` cover:

- **s01–s02**: Basic echo tool invocation (tool routing through Gateway).
- **s03–s05**: Multi-turn memory continuity — later turns reference facts from
  earlier turns, testing that AgentCore Memory retains conversation history.
- **s06–s08**: Repeated tool calls to confirm the Gateway routes every call.
- **s09**: Graceful degradation — the agent uses a tool, then answers a
  no-tool question without inventing a call, then uses the tool again.
- **s10**: No-tool baseline — the agent answers from knowledge alone, then
  uses a tool, proving it doesn't invent calls when none are needed.

## What to record

| Metric | Where it comes from |
|---|---|
| Pass rate over 10 sessions | expected vs actual outcome per session |
| Cold start (ms) | first invocation after deploy |
| Warm p50 (ms) | mid-session invocations |
| Cost per session (USD) | invocation + memory + gateway pricing, as of the run date |
| Tool calls via Gateway / direct | Gateway invocation records vs target access logs |

## Falsification

State the result that would kill the claim before running:

- If any tool call reaches a target without traversing the Gateway, the thesis
  fails regardless of pass rate. Report it.
- If the pass rate is identical with tools attached directly, then Gateway bought
  governance and revocability but **not** capability — say that plainly instead of
  implying an accuracy win.
- Cold start should be reported separately from warm latency, never averaged in.
  Averaging hides it, and cold start is usually the number that decides whether a
  design is deployable.

## Recording

Fill in `RESULTS.md` with the metric, the exact command, the commit SHA, and the
date. Same-client rule as every other measurement in this portfolio: one harness,
both configurations, so the comparison is of the mechanism and not of the tooling.
