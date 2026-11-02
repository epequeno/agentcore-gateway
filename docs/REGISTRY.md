# Registry: publishing the agent and its tools

Phase 5 turns the deployed stack into a discoverable catalog entry. The Runtime
and Gateway stay the source of truth; Registry is a searchable projection of
them, not a second deployment.

## What is published

| Record | Type | What it contains |
|---|---|---|
| `agentcore-gateway-agent` | `AGENT` | runtime ARN, gateway id/URL, model, and the names of all seven tools |
| `agentcore-gateway-tools` | `MCP` | the Gateway MCP server descriptor plus every tool's name, description and input schema |

Both are declared in `infra/stacks/agentcore.py` and created by `cdk deploy`.
The registry is named `agentcore-gateway-catalog`; its IDs are stack outputs,
never checked into git.

The `AGENT` record uses a **custom descriptor** because the Runtime is an
AgentCore HTTP runtime, not an A2A endpoint. The registry accepts
`AGENT` + `custom` for this case; the descriptor carries the deployed
`runtimeArn` so a consumer can connect the catalog entry to the exact Runtime.
A2A cards would be the right shape for an A2A server, and this repo does not
pretend the agent implements A2A.

The tools record uses the MCP server descriptor, schema `2025-12-11`, with the
MCP tools payload under `additionalData.tools` (`2024-11-05`). Tool names are
the Gateway-prefixed names (`crm-mcp___getAccount`, ...), which are the names a
consumer gets from Gateway `tools/list` and the names the agent sees.

## Publish is a separate admin step

`CfnRegistryRecord` creates records in `DRAFT`; CloudFormation has no property
that submits them. The registry is configured with `APPROVE_ALL`, so exactly one
submission moves a record to `APPROVED`:

```sh
export AGENTCORE_REGISTRY_ARN=<from just deploy outputs>
just registry-publish   # submits DRAFT records; idempotent for APPROVED
```

`just registry-check` is deliberately **data-plane only**: it uses a new
`agent-registry` client, sends a natural-language query, and does not know the
record IDs or names. It reads the approved records, compares the agent
descriptor's `runtimeArn` to `AGENTCORE_RUNTIME_ARN`, and verifies all seven
tools are present.

```sh
export AGENTCORE_REGISTRY_ARN=<from just deploy outputs>
export AGENTCORE_RUNTIME_ARN=<from just deploy outputs>
just registry-check
```

## What was measured

On 2026-11-02, after `just deploy` and `just registry-publish`:

- the agent query `support operations agent that answers customer questions
  about accounts and billing using CRM and workflow tools` returned
  `agentcore-gateway-agent` as `AGENT`;
- its custom descriptor's `runtimeArn` matched the deployed Runtime ARN;
- the tools query returned `agentcore-gateway-tools` as `MCP` with all seven
  Gateway tools;
- both records reported `APPROVED`.

`just registry-check` retries because Registry search is eventually consistent:
approval can take a few seconds, occasionally minutes, before a record appears
in `SearchDiscoverableRegistryRecords`. The control plane (`GetRegistryRecord`)
is immediately consistent; the data plane is not.

## Region

The Registry control and data-plane endpoints are available in `us-east-1` in
this account as of 2026-11-02; the smoke test and the full deploy both succeeded
there. The roadmap's earlier note that GA was only in `us-east-2`,
`eu-central-1`, `eu-west-2` and `ca-central-1` is stale for this account. The
stack still uses the same region as Runtime and Gateway, which keeps the
deployment one-region.

## Limits

- **The tool list is a projection, not a live call.** `GATEWAY_CATALOG_TOOLS`
  in the stack is updated with the target surface. If a target is added or
  removed, the record must be redeployed; Registry does not call Gateway at
  read time. The agent's `runtimeArn` in the record is generated from the
  deployed Runtime attribute, so that part cannot drift.
- **Auto-approval is a demo control, not a production review.** `APPROVE_ALL`
  makes the publish step one submission. A real catalog would keep
  `PENDING_APPROVAL` and gate `registry-publish` behind a curator; the API and
  record shape are the same.
- **The search index is eventually consistent.** A just-approved record can be
  missing from search for a short period even though `GetRegistryRecord` says
  `APPROVED`. The check retries; it does not weaken the assertion.
- **One record per resource, one region.** There is no cross-region replication
  or A2A mesh in this phase.
