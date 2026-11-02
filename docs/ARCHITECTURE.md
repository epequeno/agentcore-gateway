# Architecture

One tool boundary: every tool call goes through AgentCore Gateway. The Runtime
owns the conversation and Memory; it does not import a target endpoint or hold a
target credential. The front-door Gateway is a second, narrower boundary used
only to route the prompt A/B experiment.

## Request path

```mermaid
flowchart TB
    U["Caller / eval harness"] -->|"IAM SigV4 + user token in payload"| R["AgentCore Runtime<br/>Strands agent"]
    C["Cognito user pool"] -->|"access token"| U

    R <-->|"session + actor id"| M["AgentCore Memory"]

    R -->|"MCP + Bearer JWT"| G["Tool Gateway<br/>CUSTOM_JWT"]
    G -->|"policy session"| P["Policy engine<br/>ENFORCE"]
    P -->|"ALLOW"| T["Gateway targets"]
    P -->|"DENY: JSON-RPC -32002"| G
    G -->|"request interceptor"| I["Interceptor Lambda<br/>copies Authorization"]
    I -->|"x-agentcore-user-token"| T

    T --> L["Lambda echo"]
    T --> Q["Policy quota<br/>Lambda"]
    T --> CRM["CRM MCP<br/>API Gateway + Lambda"]
    T --> OAS["OpenAPI status<br/>Function URL + API key"]
    T --> SFN["Step Functions<br/>one pinned workflow"]
    T --> TOOLBOX["Toolbox MCP Runtime"]
    TOOLBOX --> CI["Code Interpreter"]
    TOOLBOX --> B["Browser"]

    R -.->|"ADOT spans"| OBS["CloudWatch<br/>Transaction Search"]
    G -.->|"decisions / invocations"| OBS
    OBS --> EV["AgentCore Evaluations<br/>built-ins + custom + FailureAnalysis"]
    OBS -.->|"optional export"| PH["Phoenix"]

    subgraph AB["Prompt A/B path"]
        F["Front-door Gateway<br/>AWS_IAM"] -->|"HTTP Runtime target"| R
        ABT["A/B test<br/>configuration bundles"] -->|"variant by session id"| F
    end

    subgraph CP["Control plane"]
        CDK["CDK stack"] --> R
        CDK --> M
        CDK --> G
        CDK --> P
        CDK --> T
        CDK --> F
        CDK --> EV
        CDK --> HR["Harness"]
        CDK --> REG["Registry records<br/>AGENT + MCP"]
    end
```

The front-door Gateway is only on the A/B path. A normal invocation reaches the
Runtime directly with IAM SigV4; the user's Cognito token travels in the payload
and the Runtime presents it to the tool Gateway, which validates it. The Runtime
itself is not JWT-authorized in this design. That is a deliberate scope limit,
not a claim of end-to-end user authentication.

## Why two gateways

| Boundary | Auth | Protocol | Owns |
|---|---|---|---|
| Tool Gateway | Cognito JWT authorizer | MCP | Tool catalog, policy engine, target revocation |
| Front-door Gateway | AWS IAM | HTTP Runtime target | A/B variant assignment only |

The tool Gateway has the only catalog the agent uses. The front door has no
tools and is not an alternate path to a target. A config bundle changes only the
Runtime's `system_prompt`; it does not move the tool boundary.

## Control plane

- **CDK** creates the Runtime, Memory, tool and front-door Gateways, targets,
  Cognito stack, policy engine, evaluators, Harness, online-evaluation config,
  Registry records, and execution roles.
- **Registry publish** is a separate admin step: records are created `DRAFT` and
  submitted for approval by `just registry-publish`. Registry is a searchable
  projection, not a second deployment.
- **Configuration bundles** are immutable service resources created by
  `just prompt-ab`; the A/B test is created and deleted outside CloudFormation.
- **Teardown** removes the stack and then deletes the log groups that
  CloudWatch retains. The shared CDK bootstrap stack and asset stores are
  account-level infrastructure; see [../RESULTS.md](../RESULTS.md).

## What is not in this diagram

- No third-party IdP or hosted consent portal; Cognito is the only user pool.
- No A2A server; the Registry `AGENT` record uses a custom descriptor.
- No Payments path; support operations do not move money.
- No multi-tenant isolation, cross-region replication, or production load
  distribution. Everything is one account, one region.
- The Registry tool list is a projection maintained in the stack, not a live
  Gateway call.

## Evidence map

| Claim | Evidence |
|---|---|
| The agent calls tools only through Gateway | [../RESULTS.md](../RESULTS.md) phase 1: 21 `execute_tool` spans, 0 direct |
| The tool sees the Cognito principal | [IDENTITY.md](IDENTITY.md) |
| Policy denies before the target runs | [POLICY.md](POLICY.md) |
| Heterogeneous targets share one boundary | [TOOL-SURFACE.md](TOOL-SURFACE.md) |
| Registry is discoverable | [REGISTRY.md](REGISTRY.md) |
| Hosting and prompt comparison | [HOSTING.md](HOSTING.md) |
| Service-by-service status and limits | [../COVERAGE.md](../COVERAGE.md) |
