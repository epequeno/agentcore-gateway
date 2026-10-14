# Results

Every number here is produced by a command in this repo and dated. A row with no
date has not been measured — and a number without its command is not evidence.

## Headline

| Metric | Value | Command | n | Date |
|---|---|---|---|---|
| Eval pass rate, 10 scripted sessions | TODO | `just eval` | 10 | — |
| Cold start (ms) | TODO | `just eval` | — | — |
| Warm p50 invocation (ms) | TODO | `just eval` | — | — |
| Cost per session (USD) | TODO | `just eval` | — | — |
| Tool calls routed via Gateway | TODO | `just eval` | — | — |

## The claim these rows are meant to support

That the tool surface lives in Gateway rather than in the agent: every tool call
in the eval must appear in the Gateway's invocation records, and **zero** tool
calls should reach a target directly. The last row is the one that tests it.

## How to reproduce

```sh
export AWS_PROFILE=arch
just deploy
just eval
just down        # do this — an idle Runtime still bills
```

Record the commit SHA, the date, and the exact command beside every number.
