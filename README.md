# agentcore-gateway

An agent's tools should not be wired into the agent. On AgentCore, the tools are
published to a **Gateway** — a managed tool server the agent discovers and calls
through — so the tool surface has one owner, one auth boundary, and one place to
turn a tool off. This repo is a Strands agent on AgentCore **Runtime** whose tools
arrive only via **Gateway**, with **Memory** holding multi-turn continuity, all
deployed by CDK.

> ### Historical — measured 2026-10-13, at `fc9abe3`
>
> 100% pass rate (10/10 sessions, 25/25 turns) · cold start **14,572 ms** · warm
> p50 **3,655 ms**. Cost per session is **not measured**. The pass rate is a
> substring check that a run with **no tool access** would also satisfy — read the
> limitations in [RESULTS.md](RESULTS.md) before quoting it.
>
> Re-baselined 2026-11-01 on `bedrock-agentcore` 1.25.2, `strands-agents` 1.63.0
> and `aws-cdk-lib` 2.275.0. Those numbers are **not** claims about the current
> tree; phase 1 replaced them with span-based evaluation.

> ### Scope — read before trusting anything here
>
> Built by reading the installed `bedrock_agentcore` 1.25.2 SDK, `strands-agents`
> 1.63.0, and `aws-cdk-lib` 2.275.0, and by verifying the Gateway Lambda target
> event contract against the AWS docs. Runtime, Gateway, Memory, Identity,
> Observability, Evaluations, Policy, Code Interpreter, Browser, Registry,
> Harness and Optimization (prompt A/B) are implemented, deployed and measured.
> Twelve of thirteen services are in use; Payments is out of scope. See
> [COVERAGE.md](COVERAGE.md) for the matrix and limits, and
> [docs/API-NOTES.md](docs/API-NOTES.md) for exactly which facts are confirmed
> and which remain deploy-time assumptions. The stack was torn down after each
> measurement session — nothing is live, so the numbers cannot be reproduced
> without redeploying.

## Start here

Read these in order; each one takes a few minutes:

1. [COVERAGE.md](COVERAGE.md) — the 13-service matrix: 12 in use, Payments out
   of scope, and the evidence and limit for every claim.
2. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the request path, the two
   Gateways, the control plane, and what is deliberately absent.
3. [RESULTS.md](RESULTS.md) — dated measurements, commands, limitations, and the
   teardown record.

The short version: a caller invokes an AgentCore Runtime; the Strands agent asks
the tool Gateway for tools; the Gateway enforces identity and policy before
reaching a target; Memory holds continuity; spans feed evaluation. A prompt A/B
uses a second, IAM-only front-door Gateway but the same Runtime and the same tool
boundary.

The agent never holds a tool's endpoint or credentials. It knows a Gateway URL and
asks. That is the whole point: revoking a tool is a Gateway operation, not a
redeploy of the agent.

## Run

```sh
uv sync                      # install (uv, Python 3.12+)
npm install -g aws-cdk       # the CDK CLI is a Node tool, not a pip package
npm install -g @aws/agentcore  # AgentCore CLI v1 (logs, traces, evals)
just verify                  # lint, tests, cdk synth
just txsearch                # one-time, account-level: spans -> CloudWatch
just deploy                  # Runtime + Memory + Gateway + evaluator + Cognito via CDK
just identity-setup          # create/reset the Cognito test user
just identity-check          # 401, catalog, principal-at-tool, optional revoke
just policy-check            # parameter deny, temporal deny, agent refusal, CloudWatch
just tool-surface            # one call through every Gateway target type
just registry-publish        # submit the agent and tool records for approval
just registry-check          # fresh data-plane consumer finds the agent
just hosting-compare         # Harness vs Runtime: wall time, tokens, inference cost
just prompt-ab               # control vs treatment prompt through the front door
just invoke                  # configuration smoke check
just eval                    # scripted sessions + batch evaluation
just eval-phoenix            # same sessions, scored in Phoenix
just down                    # tear down and remove retained app log groups
just teardown-check          # assert no app resources or app log groups remain
just secret-scan             # scan the source tree for ids, ARNs, and keys
just ship-check              # verify + teardown check + secret scan
export AWS_PROFILE=arch      # profiles are local; never committed
```

## Status

- [x] scaffold
- [x] Gateway created, with at least one tool target
- [x] agent calls the tool **only** through Gateway
- [x] Memory holding multi-turn continuity across sessions
- [x] AgentCore Identity — the Gateway validates the user's Cognito token, a
      request interceptor forwards it to an IAM-only MCP target, and the tool
      resolves the same `sub` through Cognito. Target deletion revokes the tool
      without a Runtime redeploy. See [docs/IDENTITY.md](docs/IDENTITY.md)
- [x] 10-session eval harness, pass rate measured (historical — see above)
- [x] cold-start ms measured (cost per session deferred to Cost Explorer)
- [x] Phase 0: dependencies re-baselined, AgentCore CLI v1 installed, old numbers
      marked historical
- [x] Phase 1: ADOT spans, AgentCore Evaluations, deterministic evaluator,
      `FailureAnalysis`, and the Phoenix comparison — measured 2026-11-01
- [~] Phase 2: identity — Cognito principal-at-tool, interceptor forwarding,
      IAM-only MCP target, target-deletion revocation — measured 2026-11-01.
      The hosted consent-portal flow is **not** delivered: the portal rejects
      Cognito sign-in with `SignInRejected`, recorded as a rejected path in
      `RESULTS.md` and [docs/IDENTITY.md](docs/IDENTITY.md)
- [x] Phase 3: Policy — parameter quota, Dogwood temporal rule, natural-language
      policy, agent refusal and CloudWatch decisions — measured 2026-11-02
- [x] Phase 4: Tool surface — Lambda, MCP server, OpenAPI and Step Functions
      targets; Code Interpreter and Browser behind a Gateway MCP target —
      measured 2026-11-02
- [x] Phase 5: Registry — agent and tools published as `AGENT`/`MCP` records;
      a fresh data-plane caller finds the agent by natural-language search and
      the descriptor's runtime ARN matches the deployment — measured 2026-11-02
- [x] Phase 6: Harness vs Runtime, and a config-bundle prompt A/B through an
      IAM front-door Gateway — measured 2026-11-02; see
      [docs/HOSTING.md](docs/HOSTING.md)
- [x] Phase 7: service coverage matrix, architecture diagram, dated results,
      and a reproducible teardown/secret check — shipped 2026-11-02; see
      [COVERAGE.md](COVERAGE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

**All phases complete.** Nothing is live. The roadmap is in
[docs/ROADMAP.md](docs/ROADMAP.md). Phase 3's design and evidence are in
[docs/POLICY.md](docs/POLICY.md); phase 4's are in
[docs/TOOL-SURFACE.md](docs/TOOL-SURFACE.md); phase 6's are in
[docs/HOSTING.md](docs/HOSTING.md).

- **A deny is a JSON-RPC error, not an HTTP failure.** The Gateway returns HTTP
  200 with a `-32002` error. The agent prompt and the policy check require the
  model to turn that into a refusal, not a generic tool error.
- **Policy session ids are caller-supplied.** The Gateway binds the id to the
  authenticated principal; the agent sends it on every MCP call. A new id resets
  temporal history, so the temporal rule is a loop guard, not anti-abuse.

## What broke and how I found it

- **AgentCore Runtime only accepts arm64 images.** The first phase 1 deploy built
  an amd64 image with the local Docker daemon and failed with "Architecture
  incompatible ... Supported platforms: [arm64]". The stack now passes
  `platform=LINUX_ARM64`; local cross-builds need buildx and qemu binfmt
  registered (`docker run --privileged tonistiigi/binfmt --install arm64`).
- **Memory turns were reversed.** `get_last_k_turns` returns newest-first, and
  the agent passed that list to the model unchanged. s04 answered "turn two"
  when asked for the first thing it echoed; the old substring harness scored it
  as a pass. `agent/memory.py` now reverses before flattening. Evaluation found
  this, not code review.
- **Batch evaluation wants evaluator ids, not ARNs.** `StartBatchEvaluation`
  rejected the ARN with a regex validation error; the harness now takes the last
  ARN segment (`EchoFidelity-abc1234567`).
- **A missing span is a failed run, not a score.** When CloudWatch had not yet
  ingested one trace, the service rejected the ground-truth context and returned
  `COMPLETED_WITH_ERRORS`. The harness now waits until every turn has an
  `invoke_agent` span before starting the batch job.
- **Dockerfile venv isolation**: `CMD ["python", ...]` used the system Python which
  couldn't see packages installed in `/app/.venv` by `uv sync`. Fixed by installing
  globally with `uv pip install --system .` and using system `python`.
- **MCPClient double-start**: `handler()` called `mcp_client.start()` then passed the
  client to `Agent(tools=[mcp_client])`, whose registry also calls `start()` via
  `load_tools()` — raising "client session is currently running". Fixed by removing
  the explicit `start()`; `load_tools()` handles the lifecycle.
- **`mcp` 2.x removed FastMCP.** The toolbox container crash-looped on
  `ModuleNotFoundError: mcp.server.fastmcp`; it pins `mcp==1.28.1`, where the
  FastMCP API still exists.
- **OpenAPI API-key providers store the whole generated secret string.** The
  Lambda receives the JSON envelope in the header; it accepts either the raw key
  or that envelope. A plain key check failed until the envelope was unwrapped.
- **Built-in tool ARNs belong to the AWS account.** Browser and Code Interpreter
  live at `...:aws:browser/...` and `...:aws:code-interpreter/...`; a role scoped
  to the caller's account gets `AccessDenied`.
- **Validation order matters.** A generated forbid was rejected as "Overly
  Restrictive" until it depended on the baseline permit. The toolbox runtime
  also needs `logs:PutResourcePolicy` during startup, not just log writes.
- **Model availability**: `us.anthropic.claude-sonnet-4-20250514-v1:0` was marked
  LEGACY; `anthropic.claude-haiku-4-5-20251001-v1:0` required a Marketplace
  subscription the account lacks; `amazon.nova-pro-v1:0` produced invalid tool-use
  sequences. `deepseek.v3.2` (ON_DEMAND, no Marketplace subscription) works with
  tool use and is the deployed default. Override with `cdk deploy -c model_id=...`.

## Notes

- The repo holds no real account IDs, deployed ARNs, or credentials. Local
  values live in `LOCAL.md`, which is gitignored. `just secret-scan` checks the
  working tree; fake test ARNs are not treated as deployed resources.
- Decisions and the reasons behind them: [docs/DECISIONS.md](docs/DECISIONS.md).
- Phase 4 target design and limits: [docs/TOOL-SURFACE.md](docs/TOOL-SURFACE.md).
- Phase 6 hosting and prompt A/B design: [docs/HOSTING.md](docs/HOSTING.md).
- Phase 5 catalog design and limits: [docs/REGISTRY.md](docs/REGISTRY.md).
