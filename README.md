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
> Built by mirroring [`clear-vial`](https://github.com/epequeno/clear-vial)'s
> working Runtime + Memory deployment, and by reading the installed
> `bedrock_agentcore` SDK rather than the release notes. **The Gateway path is not
> yet verified against the AWS documentation** — see
> [docs/API-NOTES.md](docs/API-NOTES.md), which lists exactly which facts are
> confirmed and which are assumptions. Do not treat the Gateway section as
> working until that table says so.

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
- [ ] Gateway created, with at least one tool target
- [ ] agent calls the tool **only** through Gateway
- [ ] Memory holding multi-turn continuity across sessions
- [ ] Identity wired for outbound credentials
- [ ] 10-session eval harness, pass rate measured
- [ ] cold-start ms and cost per session measured

## What broke and how I found it

*Populate as it happens. This is the section a reviewer actually reads.*

## Notes

- The repo holds no account IDs, ARNs, or credentials. Local values live in
  `LOCAL.md`, which is gitignored.
- Decisions and the reasons behind them: [docs/DECISIONS.md](docs/DECISIONS.md).
