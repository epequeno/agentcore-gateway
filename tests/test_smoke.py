"""Smoke tests: configuration and identity helpers. No AWS calls."""

from agent.config import DEFAULT_REGION, load
from agent.memory import DEFAULT_ACTOR, session_ids


def test_region_defaults_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    assert load().region == DEFAULT_REGION


def test_session_is_stable_per_actor() -> None:
    actor, session = session_ids({"actorId": "a1"})
    assert actor == "a1"
    assert session == "session-a1"


def test_missing_actor_is_not_empty() -> None:
    # Memory cannot scope on an empty string, so the fallback must produce one.
    actor, session = session_ids({})
    assert actor == DEFAULT_ACTOR
    assert session != ""


def test_explicit_session_id_wins() -> None:
    _, session = session_ids({"actorId": "a1", "sessionId": "s9"})
    assert session == "s9"


def test_policy_session_id_keeps_safe_values() -> None:
    # The Gateway accepts only [A-Za-z0-9-] up to 128 characters.
    from agent.app import policy_session_id

    assert policy_session_id("eval-s01-abc123") == "eval-s01-abc123"


def test_policy_session_id_replaces_unsafe_values() -> None:
    from agent.app import policy_session_id

    first = policy_session_id("conversation/with spaces & symbols")
    second = policy_session_id("conversation/with spaces & symbols")
    assert first == second
    assert first.startswith("session-")
    assert len(first) <= 128
    assert all(c.isalnum() or c == "-" for c in first)


def test_gateway_url_unset_defaults_empty(monkeypatch) -> None:
    monkeypatch.delenv("AGENTCORE_GATEWAY_URL", raising=False)
    assert load().gateway_url == ""


def test_gateway_url_round_trips(monkeypatch) -> None:
    monkeypatch.setenv(
        "AGENTCORE_GATEWAY_URL", "https://abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com"
    )
    assert load().gateway_url == "https://abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com"


def test_gateway_mcp_url_appends_mcp(monkeypatch) -> None:
    # The CDK Gateway.gateway_url attribute is the base URL; the MCP
    # endpoint lives at /mcp on it. settings is a module-level singleton loaded
    # at import time, so patch the attribute directly.
    from agent import config
    from agent.app import _gateway_mcp_url

    monkeypatch.setattr(
        config.settings,
        "gateway_url",
        "https://gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
    )
    assert _gateway_mcp_url() == "https://gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


def test_gateway_mcp_url_empty_when_unset(monkeypatch) -> None:
    from agent import config
    from agent.app import _gateway_mcp_url

    monkeypatch.setattr(config.settings, "gateway_url", "")
    assert _gateway_mcp_url() == ""


def test_gateway_mcp_url_idempotent(monkeypatch) -> None:
    # If the URL already ends with /mcp, don't append it again.
    from agent import config
    from agent.app import _gateway_mcp_url

    monkeypatch.setattr(config.settings, "gateway_url", "https://gw.example.com/mcp")
    assert _gateway_mcp_url() == "https://gw.example.com/mcp"


class _Msg:
    def __init__(self, role: str, text: str) -> None:
        self._data = {"role": role, "content": {"text": text}}


def test_memory_turns_are_reversed_into_chronological_order() -> None:
    # get_last_k_turns returns newest-first; passing that to the model reversed
    # the conversation and made s04 answer with the wrong turn.
    from agent.memory import _turns_to_messages

    newest_first = [
        [_Msg("USER", "third"), _Msg("ASSISTANT", "third answer")],
        [_Msg("USER", "first"), _Msg("ASSISTANT", "first answer")],
    ]
    messages = _turns_to_messages(newest_first)
    assert [m["content"][0]["text"] for m in messages] == [
        "first",
        "first answer",
        "third",
        "third answer",
    ]


def test_handler_rejects_a_missing_user_token(monkeypatch) -> None:
    # The Gateway now requires a JWT; an invocation without one must fail
    # before any tool call, not fall back to a model-only answer.
    from agent import app as app_module

    monkeypatch.setattr(app_module.settings, "model_id", "test-model")
    monkeypatch.setattr(app_module.settings, "gateway_url", "https://gw.example.com")
    result = app_module.handler({"prompt": "hello", "sessionId": "s" * 33})
    assert "authToken" in result["error"]
