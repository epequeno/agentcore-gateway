"""Session and actor identity for AgentCore Memory.

AgentCore Memory scopes long-term memory per actor and per session, so every
invocation has to carry both. The SDK surface is ``bedrock_agentcore.memory``
(client, session, controlplane) — verified against the installed SDK, 1.4.6.

Both ids come from the caller. Neither is derived from the prompt, and neither
belongs in a tool signature: anything in a tool signature is also in the model's
context.
"""

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
