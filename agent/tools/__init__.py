"""Tool surface.

Tools are meant to be published to AgentCore Gateway and discovered by the agent
at runtime — not imported into the agent. ``echo`` exists only so the repo has
one checkable tool while the Gateway path is being built.

The distinction matters: an imported tool cannot be revoked without redeploying
the agent, and its endpoint and credentials live in the agent's process. That is
the arrangement this repo is trying to eliminate.
"""

from agent.tools.echo import echo

__all__ = ["echo"]
