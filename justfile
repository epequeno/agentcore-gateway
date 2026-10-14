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

synth: sync
    @cd infra && uv run cdk synth --quiet

# Runtime + Memory + Gateway + Lambda tool target.
deploy: sync
    @cd infra && uv run cdk deploy --require-approval never

invoke: sync
    @uv run python -m evals.run --smoke

# Run the ten-session eval against a deployed Runtime. Set env vars first:
# export AGENTCORE_RUNTIME_ARN=<arn> AGENTCORE_GATEWAY_URL=<url> AGENTCORE_MEMORY_ID=<id>
eval: sync
    @uv run python -m evals.run

# Always run this. An idle AgentCore Runtime still bills.
down: sync
    @cd infra && uv run cdk destroy --force

check: verify
