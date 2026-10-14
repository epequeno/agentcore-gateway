"""AgentCore Runtime entrypoint.

Shape mirrors a working deployment: ``BedrockAgentCoreApp`` is a Starlette app
with an ``@app.entrypoint`` handler that takes a payload dict and returns a dict
(``bedrock_agentcore.runtime``; verified against the installed SDK, 1.24.1).

The agent's tools arrive through AgentCore Gateway, not through imports. On
each invocation the handler connects an ``MCPClient`` to the Gateway's MCP
endpoint (SigV4-signed streamable HTTP), loads the tool catalog from the
Gateway, and passes those tools to a Strands ``Agent``. When the Gateway URL
is unset the handler fails visibly rather than answering from the model alone
— which is the failure mode this repo exists to remove.

Verified API surface (installed SDKs):
  - ``strands.tools.mcp.MCPClient`` — context manager; ``start()`` opens a
    background thread, ``load_tools()`` calls the Gateway's ``tools/list``.
  - ``mcp_proxy_for_aws.client.aws_iam_streamablehttp_client`` — SigV4-signed
    streamable HTTP transport; ``endpoint``, ``aws_service``, ``aws_region``.
  - Gateway URL from CDK ``Gateway.gateway_url``; the MCP endpoint is
    ``{gateway_url}/mcp``.
"""

import logging

from bedrock_agentcore import BedrockAgentCoreApp
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from agent.config import settings
from agent.memory import retrieve_turns, session_ids, store_turn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = """\
You are a demonstration agent whose tools are supplied by AgentCore Gateway. \
If you have no tools available, say so plainly rather than inventing a result."""


def _gateway_mcp_url() -> str:
    """Return the Gateway's MCP endpoint URL.

    The CDK ``Gateway.gateway_url`` attribute is the base URL; the MCP
    endpoint lives at ``/mcp`` on it.
    """
    url = settings.gateway_url
    if not url:
        return ""
    return url if url.endswith("/mcp") else f"{url}/mcp"


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

    mcp_url = _gateway_mcp_url()
    if not mcp_url:
        return {
            "error": "AGENTCORE_GATEWAY_URL is not set — the agent has no tools",
            "sessionId": session,
        }

    # Connect to the Gateway via SigV4-signed streamable HTTP. The Runtime
    # role's credentials are used automatically — no secret lives in the agent.
    mcp_client = MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=mcp_url,
            aws_service="bedrock-agentcore",
            aws_region=settings.region,
        ),
    )

    try:
        # Retrieve prior conversation from AgentCore Memory so the agent
        # has multi-turn continuity across invocations.
        prior_messages = retrieve_turns(actor, session)

        # The Agent constructor's registry calls load_tools() which starts
        # the MCP client and lists tools from the Gateway — no explicit
        # start() needed. The tool catalog arrives from the Gateway's
        # tools/list, not from any import.
        agent = Agent(
            model=BedrockModel(model_id=settings.model_id, region_name=settings.region),
            system_prompt=SYSTEM_PROMPT,
            tools=[mcp_client],
            messages=prior_messages,
        )
        result = agent(prompt)
        response = str(result)

        # Store this turn in AgentCore Memory for future invocations.
        store_turn(actor, session, prompt, response)

        logger.info(
            "invocation complete",
            extra={"actor": actor, "session": session},
        )
        return {"response": response, "sessionId": session, "actorId": actor}
    finally:
        mcp_client.stop(None, None, None)


if __name__ == "__main__":
    app.run()
