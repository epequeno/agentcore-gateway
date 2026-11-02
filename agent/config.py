"""Runtime configuration, read from the environment.

Deliberately no account ids, ARNs, or credentials: those live in LOCAL.md
(gitignored) or in the shell environment. Anything hard-coded here would end up
in a public repo.
"""

import os

from pydantic import BaseModel

DEFAULT_REGION = "us-east-1"


class Settings(BaseModel):
    """Values the agent and the eval harness need at runtime."""

    region: str = DEFAULT_REGION
    model_id: str = ""
    gateway_url: str = ""
    memory_id: str = ""
    runtime_arn: str = ""
    evaluator_arn: str = ""
    rubric_evaluator_arn: str = ""
    judge_model_id: str = "deepseek.v3.2"
    user_pool_id: str = ""
    cognito_client_id: str = ""
    identity_user: str = "support-user"
    identity_password: str = ""
    gateway_id: str = ""
    policy_engine_arn: str = ""
    registry_arn: str = ""
    harness_arn: str = ""
    ab_test_role_arn: str = ""
    front_gateway_id: str = ""
    front_gateway_arn: str = ""
    front_gateway_url: str = ""
    online_eval_arn: str = ""
    online_eval_id: str = ""


def load() -> Settings:
    """Build settings from the process environment."""
    return Settings(
        region=os.environ.get("AWS_REGION", DEFAULT_REGION),
        model_id=os.environ.get("MODEL_ID", ""),
        gateway_url=os.environ.get("AGENTCORE_GATEWAY_URL", ""),
        memory_id=os.environ.get("AGENTCORE_MEMORY_ID", ""),
        runtime_arn=os.environ.get("AGENTCORE_RUNTIME_ARN", ""),
        evaluator_arn=os.environ.get("AGENTCORE_EVALUATOR_ARN", ""),
        rubric_evaluator_arn=os.environ.get("AGENTCORE_RUBRIC_EVALUATOR_ARN", ""),
        judge_model_id=os.environ.get("JUDGE_MODEL_ID", "deepseek.v3.2"),
        user_pool_id=os.environ.get("COGNITO_USER_POOL_ID", ""),
        cognito_client_id=os.environ.get("COGNITO_CLIENT_ID", ""),
        identity_user=os.environ.get("IDENTITY_TEST_USER", "support-user"),
        identity_password=os.environ.get("IDENTITY_TEST_PASSWORD", ""),
        gateway_id=os.environ.get("AGENTCORE_GATEWAY_ID", ""),
        policy_engine_arn=os.environ.get("POLICY_ENGINE_ARN", ""),
        registry_arn=os.environ.get("AGENTCORE_REGISTRY_ARN", ""),
        harness_arn=os.environ.get("AGENTCORE_HARNESS_ARN", ""),
        ab_test_role_arn=os.environ.get("AGENTCORE_AB_TEST_ROLE_ARN", ""),
        front_gateway_id=os.environ.get("AGENTCORE_FRONT_GATEWAY_ID", ""),
        front_gateway_arn=os.environ.get("AGENTCORE_FRONT_GATEWAY_ARN", ""),
        front_gateway_url=os.environ.get("AGENTCORE_FRONT_GATEWAY_URL", ""),
        online_eval_arn=os.environ.get("AGENTCORE_ONLINE_EVAL_ARN", ""),
        online_eval_id=os.environ.get("AGENTCORE_ONLINE_EVAL_ID", ""),
    )


settings = load()
