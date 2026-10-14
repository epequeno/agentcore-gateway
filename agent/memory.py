"""Session, actor identity, and memory continuity for AgentCore Memory.

AgentCore Memory scopes long-term memory per actor and per session, so every
invocation has to carry both. The SDK surface is ``bedrock_agentcore.memory``
(``MemorySessionManager``, ``ConversationalMessage``, ``MessageRole``) — verified
against the installed SDK, 1.24.1.

Both ids come from the caller. Neither is derived from the prompt, and neither
belongs in a tool signature: anything in a tool signature is also in the model's
context.

Memory continuity flow:
  1. Before invocation: ``retrieve_turns()`` fetches prior conversation from
     AgentCore Memory as a list of Strands-compatible message dicts.
  2. Those messages are passed to ``Agent(messages=...)`` so the agent has
     multi-turn context.
  3. After invocation: ``store_turn()`` writes the user prompt and agent
     response back to AgentCore Memory so the next call can recall them.
"""

import logging
from typing import Any

from bedrock_agentcore.memory import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

from agent.config import settings

logger = logging.getLogger(__name__)

DEFAULT_ACTOR = "anonymous"


def session_ids(payload: dict) -> tuple[str, str]:
    """Return ``(actor_id, session_id)`` for an invocation payload.

    A missing sessionId falls back to a stable per-actor value so that repeat
    invocations from the same actor continue one conversation rather than
    creating a new one each time.
    """
    actor = str(payload.get("actorId") or DEFAULT_ACTOR)
    session = str(payload.get("sessionId") or f"session-{actor}")
    return actor, session


def _memory_manager() -> MemorySessionManager | None:
    """Create a MemorySessionManager if AGENTCORE_MEMORY_ID is configured."""
    if not settings.memory_id:
        return None
    return MemorySessionManager(
        memory_id=settings.memory_id,
        region_name=settings.region,
    )


def retrieve_turns(actor: str, session: str, k: int = 10) -> list[dict[str, Any]]:
    """Fetch prior conversation turns from AgentCore Memory.

    Returns a list of Strands-compatible message dicts:
    ``[{"role": "user", "content": [{"text": "..."}]}, ...]``

    Returns an empty list if Memory is not configured or the session is new.
    """
    manager = _memory_manager()
    if manager is None:
        return []

    try:
        turns = manager.get_last_k_turns(actor_id=actor, session_id=session, k=k)
    except Exception as e:
        logger.warning("Memory retrieval failed: %s", e)
        return []

    messages: list[dict[str, Any]] = []
    for turn in turns:
        for event_message in turn:
            # EventMessage wraps {"role": "USER"|"ASSISTANT", "content": {"text": "..."}}
            raw = event_message._data if hasattr(event_message, "_data") else event_message
            role_str = str(raw.get("role", "")).lower()
            text = raw.get("content", {}).get("text", "")
            if not text:
                continue
            # Strands uses "user" and "assistant" (lowercase)
            role = "user" if role_str == "user" else "assistant"
            messages.append({"role": role, "content": [{"text": text}]})

    logger.info("Retrieved %d messages from memory", len(messages))
    return messages


def store_turn(actor: str, session: str, user_prompt: str, agent_response: str) -> None:
    """Store one conversation turn (user prompt + agent response) in AgentCore Memory."""
    manager = _memory_manager()
    if manager is None:
        return

    try:
        manager.add_turns(
            actor_id=actor,
            session_id=session,
            messages=[
                ConversationalMessage(text=user_prompt, role=MessageRole.USER),
                ConversationalMessage(text=agent_response, role=MessageRole.ASSISTANT),
            ],
        )
        logger.info("Stored turn in memory for actor=%s session=%s", actor, session)
    except Exception as e:
        logger.warning("Memory storage failed: %s", e)
