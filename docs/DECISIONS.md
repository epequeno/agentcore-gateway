# Decisions

Short log of choices that would otherwise be re-litigated. Newest last.

## 2026-10-13 · The Gateway is the deliverable, not the agent

A working Runtime + Memory deployment already exists in a sibling project, so
reproducing it teaches nothing. The gap is Gateway: it is the one part of the
AgentCore trio that has never been used, and it is what the job descriptions
currently ask for by name. So the agent here is deliberately boring, and the
Gateway is where the work is.

## 2026-10-13 · Tools are not imported into the agent

`agent/tools/` holds one placeholder so the repo has something checkable. Real
tools belong in a Gateway target, not in this process. An imported tool cannot be
revoked without redeploying the agent, and its endpoint and credentials live in
the agent's memory — which is exactly the arrangement this repo exists to remove.

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
