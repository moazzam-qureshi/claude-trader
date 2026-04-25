"""Stub `claude` binary for integration tests.

Reads the JSON response from env var FAKE_CLAUDE_RESPONSE and emits it as the
final stdout line, mimicking what `claude -p` would print.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    resp = os.environ.get("FAKE_CLAUDE_RESPONSE")
    if not resp:
        print("FAKE_CLAUDE_RESPONSE not set", file=sys.stderr)
        return 2
    print("(fake-claude) triaging...")
    print(resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
