from types import SimpleNamespace

from agent import app as agent_app
from agent.prompts import BASELINE_SYSTEM_PROMPT


class FakeAgent:
    def __init__(self):
        self.system_prompt = BASELINE_SYSTEM_PROMPT


def test_bundle_prompt_overrides_the_default(monkeypatch):
    monkeypatch.setattr(
        agent_app.BedrockAgentCoreContext,
        "get_config_bundle",
        classmethod(lambda cls: {"system_prompt": "treatment prompt"}),
    )
    agent = FakeAgent()

    agent_app.apply_config_bundle(SimpleNamespace(agent=agent))

    assert agent.system_prompt == "treatment prompt"


def test_missing_bundle_keeps_the_default(monkeypatch):
    monkeypatch.setattr(
        agent_app.BedrockAgentCoreContext,
        "get_config_bundle",
        classmethod(lambda cls: {}),
    )
    agent = FakeAgent()

    agent_app.apply_config_bundle(SimpleNamespace(agent=agent))

    assert agent.system_prompt == BASELINE_SYSTEM_PROMPT


def test_bundle_failure_keeps_the_default(monkeypatch):
    def fail(cls):
        raise RuntimeError("bundle service unavailable")

    monkeypatch.setattr(agent_app.BedrockAgentCoreContext, "get_config_bundle", classmethod(fail))
    agent = FakeAgent()

    agent_app.apply_config_bundle(SimpleNamespace(agent=agent))

    assert agent.system_prompt == BASELINE_SYSTEM_PROMPT
