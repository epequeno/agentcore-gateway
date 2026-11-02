"""AgentCore Runtime entrypoint.

Shape mirrors a working deployment: ``BedrockAgentCoreApp`` is a Starlette app
with an ``@app.entrypoint`` handler that takes a payload dict and returns a dict
(``bedrock_agentcore.runtime``; verified against the installed SDK, 1.24.1).

The agent's tools arrive through AgentCore Gateway, not through imports. On
each invocation the handler connects an ``MCPClient`` to the Gateway's MCP
endpoint with the caller's Cognito access token (JWT inbound auth), loads the
tool catalog from the Gateway, and passes those tools to a Strands ``Agent``.
When the Gateway URL or the user token is unset the handler fails visibly
rather than answering from the model alone — which is the failure mode this
repo exists to remove. The Gateway, not the agent, decides whether the token is
valid and which tools it may call.

Verified API surface (installed SDKs):
  - ``strands.tools.mcp.MCPClient`` supports ``url=`` and ``headers=`` directly;
    it opens a streamable-HTTP connection and ``load_tools()`` calls the
    Gateway's ``tools/list``.
  - Gateway URL from CDK ``Gateway.gateway_url``; the MCP endpoint is
    ``{gateway_url}/mcp``.
"""

import hashlib
import logging
import re

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.runtime import BedrockAgentCoreContext
from strands import Agent
from strands.hooks.events import BeforeModelCallEvent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from agent.config import settings
from agent.memory import retrieve_turns, session_ids, store_turn
from agent.observability import configure_telemetry, trace_session
from agent.prompts import BASELINE_SYSTEM_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Must run before the first Agent is constructed: Strands grabs the global
# tracer provider once, and ADOT may already have installed one.
configure_telemetry()

app = BedrockAgentCoreApp()


def apply_config_bundle(event: BeforeModelCallEvent) -> None:
    """Apply the A/B configuration bundle's prompt before each model call.

    The Gateway assigns a configuration bundle to a session during an
    AgentCore A/B test and passes its reference in W3C baggage. Outside a test
    there is no reference and this is a no-op. A service or network failure
    falls back to the deployed default rather than failing the user's turn.
    """
    ref = BedrockAgentCoreContext.get_config_bundle_ref()
    if ref is not None:
        logger.info("config bundle ref %s version %s", ref.bundle_arn, ref.bundle_version)
    try:
        config = BedrockAgentCoreContext.get_config_bundle()
    except Exception as exc:  # noqa: BLE001 - bundle service is optional
        logger.warning("config bundle unavailable: %s", exc)
        return
    prompt = config.get("system_prompt") if isinstance(config, dict) else None
    if prompt:
        event.agent.system_prompt = prompt


def policy_session_id(session: str) -> str:
    """Return a Gateway policy session id that satisfies its header format.

    The Gateway accepts 1-128 characters from ``[A-Za-z0-9-]``. Runtime session
    ids are caller-supplied and may contain other characters, so anything
    outside that alphabet is replaced by a stable hash rather than silently
    dropped, which would merge unrelated sessions.
    """
    if re.fullmatch(r"[A-Za-z0-9-]{1,128}", session):
        return session
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()[:32]
    return f"session-{digest}"


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

    Accepted payload keys: ``prompt`` (str, required), ``authToken`` (str,
    required — the caller's Cognito access token), ``actorId`` (str),
    ``sessionId`` (str).
    """
    actor, session = session_ids(payload)
    prompt = payload.get("prompt", "")
    bundle_ref = BedrockAgentCoreContext.get_config_bundle_ref()
    if bundle_ref is not None:
        logger.info(
            "runtime bundle ref arn=%s version=%s",
            bundle_ref.bundle_arn,
            bundle_ref.bundle_version,
        )
    auth_token = payload.get("authToken") or payload.get("auth_token", "")

    if not settings.model_id:
        # Fail visibly. A silent empty answer would read as a working agent.
        return {
            "error": "MODEL_ID is not set — see .env.example",
            "sessionId": session,
        }

    if not prompt:
        return {"error": "payload.prompt is required", "sessionId": session}

    if not auth_token:
        return {
            "error": "payload.authToken is required — the Gateway rejects unauthenticated calls",
            "sessionId": session,
        }

    mcp_url = _gateway_mcp_url()
    if not mcp_url:
        return {
            "error": "AGENTCORE_GATEWAY_URL is not set — the agent has no tools",
            "sessionId": session,
        }

    # Connect with the caller's bearer token. The Gateway validates it and binds
    # the resulting principal to every tool call; the Runtime role is not used
    # as a credential here.
    mcp_client = MCPClient(
        url=mcp_url,
        headers={
            "Authorization": f"Bearer {auth_token}",
            "x-amzn-bedrock-agentcore-policy-session-id": policy_session_id(session),
        },
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
            system_prompt=BASELINE_SYSTEM_PROMPT,
            tools=[mcp_client],
            messages=prior_messages,
            trace_attributes={"session.id": session},
        )
        agent.hooks.add_callback(BeforeModelCallEvent, apply_config_bundle)
        with trace_session(session):
            result = agent(prompt)
        response = str(result)
        usage = getattr(result.metrics, "accumulated_usage", {}) or {}

        # Store this turn in AgentCore Memory for future invocations.
        store_turn(actor, session, prompt, response)

        logger.info(
            "invocation complete",
            extra={"actor": actor, "session": session},
        )
        return {
            "response": response,
            "sessionId": session,
            "actorId": actor,
            "usage": dict(usage),
        }
    finally:
        mcp_client.stop(None, None, None)


if __name__ == "__main__":
    app.run()
