# evals — the headline number

**Not implemented.** `just eval` exits non-zero on purpose rather than passing
silently, so the gap stays visible.

## The claim being tested

That the tool surface lives in **Gateway**, not in the agent. Two halves:

1. The ten scripted multi-turn sessions produce the expected outcome at a
   measurable pass rate.
2. Every tool call in those sessions appears in the Gateway's records, and
   **zero** tool calls reach a target directly.

Half two is the one that tests the thesis. A pass rate can improve for reasons
unrelated to where the tools live; only call attribution distinguishes the
mechanism from a coincidence.

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
