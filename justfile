# agentcore-gateway: a Strands agent whose tools live behind AgentCore Gateway
set shell := ["bash", "-uc"]

default:
    @just --list

# Fail loudly if uv is missing, rather than failing confusingly in a later recipe.
toolchain:
    @command -v uv >/dev/null || { echo "uv not installed — see https://docs.astral.sh/uv/"; exit 1; }
    @uv --version

sync: toolchain
    @uv sync --quiet

lint: sync
    @uv run ruff check .
    @uv run ruff format --check .

fmt: sync
    @uv run ruff format .
    @uv run ruff check --fix .

test: sync
    @uv run pytest

# The gate: lint, tests, and the CDK stack must synthesise.
verify: lint test synth

# The CDK CLI is a Node tool, not a pip package: aws-cdk-lib provides the
# constructs but not the executable. Fail loudly rather than with a bare
# "Failed to spawn: cdk".
cdk-toolchain:
    @command -v cdk >/dev/null || { echo "cdk CLI missing — run: npm install -g aws-cdk"; exit 1; }
    @cdk --version

synth: sync cdk-toolchain
    @cd infra && uv run cdk synth --quiet

# Runtime + Memory + Gateway + Lambda tool target.
deploy: sync cdk-toolchain
    @cd infra && uv run cdk deploy --require-approval never

# Configuration check only.
invoke: sync
    @uv run python -m evals.run --smoke

# After `just deploy`: create the Cognito test user.
# Requires IDENTITY_TEST_PASSWORD in the environment.
identity-setup: sync
    @uv run python scripts/identity_setup.py

# Phase 2 evidence: unauthenticated rejection, authenticated catalog, principal
# at the MCP tool. `--revoke` deletes the target and checks the catalog again.
identity-check: sync
    @uv run python -m evals.identity

# One-time, account-level: X-Ray -> CloudWatch Logs for spans.
txsearch:
    @bash scripts/enable_transaction_search.sh

# Phase 3 evidence: parameter deny, temporal deny, agent refusal, CloudWatch.
policy-check: sync
    @uv run python -m evals.policy

# Phase 4 evidence: one call through every target type, plus Gateway metrics.
tool-surface: sync
    @uv run python -m evals.tools

# Submit the stack's DRAFT catalog records; APPROVE_ALL publishes them.
# Requires AGENTCORE_REGISTRY_ARN from `just deploy`.
registry-publish: sync
    @uv run python scripts/registry_publish.py

# Phase 5 evidence: fresh data-plane consumer discovers the agent and tools,
# and the agent descriptor's Runtime ARN matches the deployed runtime.
# Exports: AGENTCORE_REGISTRY_ARN and AGENTCORE_RUNTIME_ARN.
registry-check: sync
    @uv run python -m evals.registry

# Phase 6 evidence: the same CRM task on Harness and Runtime, with wall time,
# tokens and list-price inference cost per session.
# Exports: AGENTCORE_HARNESS_ARN, AGENTCORE_RUNTIME_ARN, AGENTCORE_GATEWAY_URL,
# COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, IDENTITY_TEST_PASSWORD.
hosting-compare: sync
    @uv run python -m evals.hosting

# Phase 6 evidence: two system prompts as immutable configuration bundles,
# A/B-tested through the Runtime front door and scored by online evaluation.
# Exports: AGENTCORE_RUNTIME_ARN, AGENTCORE_FRONT_GATEWAY_ARN,
# AGENTCORE_ONLINE_EVAL_ARN, AGENTCORE_AB_TEST_ROLE_ARN and the Cognito values.
prompt-ab: sync
    @uv run python -m evals.optimization

# Delete A/B tests and prompt bundles created outside CloudFormation.
phase6-cleanup: sync
    @uv run python scripts/cleanup_phase6.py

# Delete log groups that survive CloudFormation stack deletion.
phase7-cleanup: sync
    @uv run python scripts/cleanup_phase7.py

# Phase 7 evidence: no application resources or app log groups remain.
teardown-check: sync
    @uv run python scripts/teardown_check.py

# Phase 7 guard: tracked files contain no account ids, deployed ARNs, or keys.
secret-scan: sync
    @uv run python scripts/secret_scan.py

# Invoke the scripted sessions, then batch-evaluate the spans they produced.
# Exports: AGENTCORE_RUNTIME_ARN, AGENTCORE_GATEWAY_URL, AGENTCORE_MEMORY_ID,
# AGENTCORE_EVALUATOR_ARN (from `just deploy` outputs).
eval: sync
    @uv run python -m evals.run --run

# Same sessions, run locally with spans exported to Phoenix for the comparison.
# Exports: PHOENIX_OTLP_ENDPOINT (default: the shared observability stack).
eval-phoenix: sync
    @uv run python -m evals.run --phoenix

# Score the local sessions in Phoenix and pair the verdicts with AgentCore's.
phoenix-compare: sync
    @uv run --extra evals python -m evals.phoenix

# Always run this. An idle AgentCore Runtime still bills.
down: sync cdk-toolchain
    @uv run python scripts/cleanup_phase6.py || true
    @cd infra && uv run cdk destroy --force
    @uv run python scripts/cleanup_phase7.py

ship-check: verify teardown-check secret-scan

check: verify
