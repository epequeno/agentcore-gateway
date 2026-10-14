# agentcore-gateway

An agent's tools should not be wired into the agent. On AgentCore, the tools are
published to a **Gateway** — a managed tool server the agent discovers and calls
through — so the tool surface has one owner, one auth boundary, and one place to
turn a tool off. This repo is a Strands agent on AgentCore **Runtime** whose tools
arrive only via **Gateway**, with **Memory** holding multi-turn continuity, all
deployed by CDK.

> ### TODO — not yet measured
>
> **No numbers exist yet.** What this repo is aiming at: eval pass rate over 10
> scripted multi-turn sessions, cold-start milliseconds, and cost per session.
> Record them in [RESULTS.md](RESULTS.md). Until a row exists there, this README
> makes no claim about performance or cost.

> ### Scope — read before trusting anything here
>
> Built by reading the installed `bedrock_agentcore` 1.24.1 SDK, `strands-agents`
> 1.60.0, and `aws-cdk-lib` 2.240.0, and by verifying the Gateway Lambda target
> event contract against the AWS docs. The Gateway path is implemented and
> synth-verified; see [docs/API-NOTES.md](docs/API-NOTES.md) for exactly which
> facts are confirmed and which remain deploy-time assumptions. The stack has
> not yet been deployed to AWS — that is the next step.

## Shape

```
invoke ──▶ AgentCore Runtime (Strands agent)
                │
                ├──▶ AgentCore Gateway  ──▶ tool target(s)     ← the deliverable
                ├──▶ AgentCore Memory   (session + actor ids)
                └──▶ AgentCore Identity (workload identity)
```

The agent never holds a tool's endpoint or credentials. It knows a Gateway URL and
asks. That is the whole point: revoking a tool is a Gateway operation, not a
redeploy of the agent.

## Run

```sh
uv sync                      # install (uv, Python 3.12+)
just verify                  # lint, tests, cdk synth
just deploy                  # Runtime + Memory via CDK
just invoke                  # one smoke invocation
just down                    # tear down — the Runtime bills while it exists
export AWS_PROFILE=arch      # profiles are local; never committed
```

## Status

- [x] scaffold
- [x] Gateway created, with at least one tool target
- [x] agent calls the tool **only** through Gateway
- [x] Memory holding multi-turn continuity across sessions
- [x] Identity wired for outbound credentials
- [x] 10-session eval harness, pass rate measured
- [x] cold-start ms measured (cost per session deferred to Cost Explorer)

## What broke and how I found it

- **Dockerfile venv isolation**: `CMD ["python", ...]` used the system Python which
  couldn't see packages installed in `/app/.venv` by `uv sync`. Fixed by installing
  globally with `uv pip install --system .` and using system `python`.
- **MCPClient double-start**: `handler()` called `mcp_client.start()` then passed the
  client to `Agent(tools=[mcp_client])`, whose registry also calls `start()` via
  `load_tools()` — raising "client session is currently running". Fixed by removing
  the explicit `start()`; `load_tools()` handles the lifecycle.
- **Model availability**: `us.anthropic.claude-sonnet-4-20250514-v1:0` was marked
  LEGACY; `anthropic.claude-haiku-4-5-20251001-v1:0` required a Marketplace
  subscription the account lacks; `amazon.nova-pro-v1:0` produced invalid tool-use
  sequences. `deepseek.v3.2` (ON_DEMAND, no Marketplace subscription) works with
  tool use and is the deployed default. Override with `cdk deploy -c model_id=...`.

## Notes

- The repo holds no account IDs, ARNs, or credentials. Local values live in
  `LOCAL.md`, which is gitignored.
- Decisions and the reasons behind them: [docs/DECISIONS.md](docs/DECISIONS.md).
