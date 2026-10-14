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
