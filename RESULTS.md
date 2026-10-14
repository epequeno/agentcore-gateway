# Results

Every number here is produced by a command in this repo and dated. A row with no
date has not been measured — and a number without its command is not evidence.

**Measured at commit `fc9abe3`**, the tree that was deployed, on 2026-10-13.
Later commits change the repo, not these numbers.

## Headline

| Metric | Value | Command | n | Date |
|---|---|---|---|---|
| Eval pass rate, 10 scripted sessions | 100% (10/10 sessions, 25/25 turns) | `just eval` | 10 | 2026-10-13 |
| Cold start (ms) | 14,572 | `just eval` | 1 | 2026-10-13 |
| Warm p50 invocation (ms) | 3,655 | `just eval` | 24 | 2026-10-13 |
| Cost per session (USD) | not measured — see AWS Cost Explorer after teardown | `just eval` | 10 | — |
| Tool calls routed via Gateway | all (confirmed via CloudWatch logs) | `aws logs tail` | — | 2026-10-13 |

## The claim these rows are meant to support

That the tool surface lives in Gateway rather than in the agent: every tool call
in the eval must appear in the Gateway's invocation records, and **zero** tool
calls should reach a target directly. The last row is the one that tests it.

## Memory continuity

Sessions s03 and s05 confirm multi-turn memory continuity via AgentCore Memory:

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
  confirmed by hand with `aws logs tail`, not by `just eval`. Automating per-turn
  tool-call attribution is the next change to make.
- **Cost per session is unmeasured.** The stack has been torn down, so it needs
  AWS Cost Explorer or a redeploy.
- **One deployment, one run.** 10 sessions and 25 turns is a smoke test, not a
  distribution; the latency figures come from 24 warm invocations.
- Cold start (14.6 s) is reported separately from warm latency rather than averaged
  in, because container start dominates it and averaging would hide it.

## How to reproduce

```sh
export AWS_PROFILE=arch
just deploy
export AGENTCORE_RUNTIME_ARN=<from deploy outputs>
export AGENTCORE_GATEWAY_URL=<from deploy outputs>
export AGENTCORE_MEMORY_ID=<from deploy outputs>
just eval
just down        # do this — an idle Runtime still bills
```

Record the commit SHA, the date, and the exact command beside every number.
