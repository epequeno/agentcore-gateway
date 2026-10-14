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


def load() -> Settings:
    """Build settings from the process environment."""
    return Settings(
        region=os.environ.get("AWS_REGION", DEFAULT_REGION),
        model_id=os.environ.get("MODEL_ID", ""),
        gateway_url=os.environ.get("AGENTCORE_GATEWAY_URL", ""),
        memory_id=os.environ.get("AGENTCORE_MEMORY_ID", ""),
        runtime_arn=os.environ.get("AGENTCORE_RUNTIME_ARN", ""),
    )


settings = load()
