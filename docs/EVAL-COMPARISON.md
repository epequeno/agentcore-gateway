# Evaluator comparison — AgentCore Evaluations vs Phoenix

Measured 2026-11-01 on the current tree (`bedrock-agentcore` 1.25.2,
`strands-agents` 1.63.0). Judge model: `deepseek.v3.2` on Bedrock, held constant
on both sides. n = 26 turns across 11 scripted sessions — a case study, not a
benchmark.

## What was compared

The same session transcripts were scored twice: once by AgentCore's batch
evaluation over spans in CloudWatch, once by Phoenix's evaluator SDK. The
Phoenix span export is separately verified (136 spans, 12 sessions with a
`session.id`) so the comparison is not an install demo. The transcripts come
from the AgentCore run in both cases: two independent executions produce
different outputs, and a judge comparison has to hold inputs fixed.

| Pair | AgentCore side | Phoenix side | Why it is in the set |
|---|---|---|---|
| Prebuilt correctness | `Builtin.Correctness` (trace level, has session context) | `CorrectnessEvaluator` (input + output only) | Do off-the-shelf judges agree? |
| Custom rubric | `GatewayRubric` (LLM-as-a-judge, trace level) | `ClassificationEvaluator` with the identical instruction text | Isolates the platform from the prompt |

Faithfulness is deliberately absent. The only tool is `echo`, so there is no
retrieved context to be faithful to. A groundedness score over an echo tool
would repeat the overclaim the old substring pass rate made; the row is cut, not
faked (see `docs/DECISIONS.md`).

## Results

| Pair | n | Raw agreement | Cohen's κ | Disagreements |
|---|---|---|---|---|
| Prebuilt correctness | 26 | 73% (19/26) | 0.117 | 7 |
| Custom rubric | 26 | 100% (26/26) | 1.000 | 0 |

Judge cost (Phoenix side, 52 calls, measured from litellm usage): **$0.0125
total, $0.00048 per turn** at the Bedrock list price for `deepseek.v3.2`
($0.62 / 1M input, $1.85 / 1M output, checked 2026-11-01). AgentCore does not
expose judge token usage in the evaluation result, so its cost per case is
**not reported by the service**; the custom row uses the same model and prompt,
so the Phoenix figure is the best available estimate of the shared cost.

## The disagreements

All seven are in the prebuilt correctness pair, and they have a single shape:
Phoenix's `CorrectnessEvaluator` sees only the current input and output, while
`Builtin.Correctness` evaluates a trace with the session context behind it.

| Case | AgentCore | Phoenix | What happened |
|---|---|---|---|
| s01 t1, s01 t2, s09 t1 | correct | incorrect | Agent response described the echo tool's HTTP 200 envelope; Phoenix judged that claim unsupported from the prompt alone. |
| s03 t2, s05 t2, s05 t4 | correct | incorrect | Memory-recall turns. Phoenix has no conversation history, so it cannot see that the recalled text came from an earlier turn. |
| s06 t2 | incorrect | correct | The agent claimed the echo tool was unavailable. The trace-level judge saw the missing tool call; the input/output judge accepted the answer. |

The custom rubric pair has no disagreements. Same prompt, same model, same
verdict, 26/26. That is the controlled row: where the two platforms are given
the same instructions, the platform choice does not change the answer.

## What this supports, and what it does not

- **Supported:** the instrumentation is portable. The same Strands spans render
  in Phoenix (span tree, prompt/completion, tool calls, token counts) and are
  readable by AgentCore Evaluations. The custom rubric transfers verbatim with
  perfect agreement.
- **Supported:** the platform choice in this repo is not an accuracy claim.
  Built-in correctness is convenient because it has session context; Phoenix is
  better suited to a local, offline loop. Neither is "more correct" in general.
- **Not supported:** a claim that one platform's scores are better. n = 26 on a
  demo workload cannot support that, and the prebuilt pair differs in its
  inputs, not just its implementation.
- **Known limitation:** the correctness pair compares two different prompts.
  That is the point of the row (off-the-shelf behavior), but it means the κ of
  0.117 measures prompt and context differences as much as platform ones. The
  rubric pair is the fair test, and it reads 1.000.

## Reproduce

```sh
export PHOENIX_OTLP_ENDPOINT=http://localhost:4318
just eval-phoenix       # same sessions locally, spans into Phoenix
just eval               # AgentCore run + batch evaluation
just phoenix-compare    # score the AgentCore transcripts in Phoenix, pair verdicts
```

`just phoenix-compare` refuses to run if Phoenix has no spans for a session, so
a broken export cannot masquerade as agreement. The full record (per-turn
verdicts, explanations, tokens, disagreements) is written to
`results/phoenix-comparison-*.json`; the committed table above is the summary.
