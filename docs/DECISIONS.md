# Decisions

Short log of choices that would otherwise be re-litigated. Newest last.

## 2026-10-13 · The Gateway is the deliverable, not the agent

A working Runtime + Memory deployment already exists in a sibling project, so
reproducing it teaches nothing. The gap is Gateway: it is the one part of the
AgentCore trio that has never been used, and it is what the job descriptions
currently ask for by name. So the agent here is deliberately boring, and the
Gateway is where the work is.

## 2026-10-13 · Tools are not imported into the agent

The echo tool now lives behind the Gateway as a Lambda target — it was
removed from `agent/tools/` so the repo has no imported tools at all. An
imported tool cannot be revoked without redeploying the agent, and its
endpoint and credentials live in the agent's memory — which is exactly the
arrangement this repo exists to remove.

## 2026-10-13 · Gateway via CDK, not the control plane

`aws_cdk.aws_bedrockagentcore` exposes `CfnGateway` and `CfnGatewayTarget`, so the
Gateway can be declared alongside the Runtime in one stack instead of being created
by a separate control-plane script. One stack means one deploy, one teardown, and
no drift between what CDK thinks exists and what a script created.

Revisit if the auth configuration turns out to need control-plane calls the
constructs do not expose — that is an open question in `API-NOTES.md`.

## 2026-10-13 · No account id anywhere in the repo

Region only, in `infra/app.py` and `cdk.json`. Account ids, ARNs, and profile names
live in `LOCAL.md`, which is gitignored, because this repo is intended to be
publishable and an account id is a durable identifier that costs nothing to keep
out.

## 2026-10-13 · Fail visibly rather than plausibly

`just eval` exits non-zero; the entrypoint returns an explicit error when
`MODEL_ID` is unset; the harness prints "not implemented" and returns 2. A scaffold
that quietly succeeds is worse than one that fails, because the success gets
mistaken for the work being done.

## 2026-10-13 · Cold start reported separately from warm latency

`evals/README.md` forbids averaging them together. Cold start is usually the number
that decides whether a design is deployable, and averaging it into p50 hides it.

## 2026-10-13 · IAM inbound auth, not Cognito

The L2 `Gateway` construct defaults to Cognito M2M inbound auth — it creates a
User Pool and client on your behalf. That works, but it means the agent needs a
client secret or a token-fetch step to call the Gateway. IAM inbound auth
(`GatewayAuthorizer.using_aws_iam()`) needs no secret: the Runtime role signs
requests with SigV4, and the Gateway validates the signature. The agent's only
credential is the role it already runs as — which is the thesis of this repo:
no tool endpoint or credential lives in the agent.

## 2026-10-13 · Lambda target, not MCP Server target

A Lambda target is fully self-contained in the CDK stack: the function, the
tool schema, and the IAM grant all live in one deploy. An MCP Server target
would front an external HTTPS endpoint (the sibling `mcp-stateless` repo),
linking the two projects — but it adds an external dependency, an HTTPS
endpoint, and OAuth2 setup that the thesis does not require. Revocation is
the point: deleting a Gateway target is a one-operation revoke, and a Lambda
target keeps that demonstration clean.

## 2026-10-13 · Plain MCPClient load_tools, not semantic search

`AgentCoreToolSearchPlugin` is the official bedrock_agentcore→Strands
integration: it derives intent via an LLM call per invocation, then searches
the Gateway's tool catalog for relevant tools. That is the right tool for
hundreds of tools. With one tool it adds an extra LLM call (cost + latency)
for no benefit. `MCPClient.start()` + `load_tools()` lists all gateway tools
once; the agent calls them only through the Gateway's `/mcp` endpoint. The
thesis — tools live behind Gateway, zero direct calls — is fully tested
either way.
