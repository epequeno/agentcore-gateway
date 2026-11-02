"""Scan tracked text files for account IDs, real ARNs, and common credentials.

This is a guard, not a secret manager: it catches the patterns that would make a
public repo unsafe, and it deliberately allows wildcard IAM resource ARNs and
the fake evaluator ARN used in a unit test.
"""

import re
import subprocess
from pathlib import Path

PATTERNS = {
    "AWS account id": re.compile(r"\b\d{12}\b"),
    "real AWS ARN": re.compile(r"arn:aws:[^:\s]+:[^:\s]*:\d{12}:"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret assignment": re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*\S+"),
}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    files = [
        path
        for path in (
            Path(item)
            for item in subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root
            )
            .decode()
            .split("\0")
            if item
        )
        if (root / path).is_file()
    ]

    hits: list[str] = []
    for relative in files:
        path = Path(root, relative)
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    hits.append(f"{relative}:{line_number}: {label}: {line.strip()[:160]}")

    if hits:
        print("FAIL: possible committed secret or account identifier:")
        print("\n".join(hits))
        return 1
    print(f"PASS: {len(files)} files scanned; no account ids, real ARNs, or keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
