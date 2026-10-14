"""A placeholder tool, standing in for one published to AgentCore Gateway."""

from strands import tool


@tool
def echo(text: str) -> str:
    """Return the text unchanged.

    Args:
        text: The text to return.

    Returns:
        The same text.
    """
    return text
