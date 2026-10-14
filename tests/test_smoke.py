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
