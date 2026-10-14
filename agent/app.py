"""AgentCore Runtime entrypoint.

Shape mirrors a working deployment: ``BedrockAgentCoreApp`` is a Starlette app
with an ``@app.entrypoint`` handler that takes a payload dict and returns a dict
(``bedrock_agentcore.runtime``; verified against the installed SDK, 1.4.6).

The agent is built here, but its tools are NOT attached here — they are meant to
arrive through AgentCore Gateway. That path is unimplemented; see
docs/API-NOTES.md.
"""

import logging

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

from agent.config import settings
from agent.memory import session_ids

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = """You are a demonstration agent whose tools are supplied by \
AgentCore Gateway. If you have no tools available, say so plainly rather than \
inventing a result."""


@app.entrypoint
def handler(payload: dict) -> dict:
    """Handle one invocation.

    Accepted payload keys: ``prompt`` (str, required), ``actorId`` (str),
    ``sessionId`` (str).
    """
    actor, session = session_ids(payload)
    prompt = payload.get("prompt", "")

    if not settings.model_id:
        # Fail visibly. A silent empty answer would read as a working agent.
        return {
            "error": "MODEL_ID is not set — see .env.example",
            "sessionId": session,
        }

    if not prompt:
        return {"error": "payload.prompt is required", "sessionId": session}

    # NOTE: no tools are passed. Gateway tool discovery is not implemented yet,
    # so this agent answers from the model alone — which is exactly the failure
    # mode this repo exists to remove.
    agent = Agent(
        model=BedrockModel(model_id=settings.model_id, region_name=settings.region),
        system_prompt=SYSTEM_PROMPT,
    )
    result = agent(prompt)
    logger.info("invocation complete", extra={"actor": actor, "session": session})

    return {"response": str(result), "sessionId": session, "actorId": actor}


if __name__ == "__main__":
    app.run()
