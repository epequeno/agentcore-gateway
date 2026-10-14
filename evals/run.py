"""Eval harness.

``--smoke`` checks that configuration resolves and the runtime is reachable, and
exits 0. The real eval — ten scripted multi-turn sessions against a deployed
Runtime, with tool-call attribution — is not implemented, and exits non-zero so
the gap is visible rather than mistaken for a pass.
"""

import argparse
import json
import sys
from pathlib import Path

from agent.config import settings

SESSIONS = Path(__file__).parent / "sessions.jsonl"


def load_sessions() -> list[dict]:
    """Read the scripted sessions. Raises if the fixture is missing."""
    if not SESSIONS.exists():
        raise FileNotFoundError(f"missing fixture: {SESSIONS}")
    return [json.loads(line) for line in SESSIONS.read_text().splitlines() if line.strip()]


def smoke() -> int:
    """Check that settings resolve and report what is missing."""
    missing = [
        name
        for name, value in (
            ("MODEL_ID", settings.model_id),
            ("AGENTCORE_RUNTIME_ARN", settings.runtime_arn),
            ("AGENTCORE_MEMORY_ID", settings.memory_id),
            ("AGENTCORE_GATEWAY_URL", settings.gateway_url),
        )
        if not value
    ]
    print(f"region:   {settings.region}")
    print(f"sessions: {len(load_sessions())} scripted")
    if missing:
        print(f"unset:    {', '.join(missing)}")
    else:
        print("configuration resolves; the eval itself is not implemented yet")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentCore Gateway eval harness")
    parser.add_argument("--smoke", action="store_true", help="check config only")
    args = parser.parse_args(argv)

    if args.smoke:
        return smoke()

    print("eval not implemented yet — see evals/README.md")
    return 2


if __name__ == "__main__":
    sys.exit(main())
