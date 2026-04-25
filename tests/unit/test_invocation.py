import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest


def test_invoke_claude_parses_last_json_line(monkeypatch, tmp_path):
    from trading_sandwich.triage.invocation import invoke_claude

    fake = Path("tests/fixtures/fake_claude.py").resolve()
    response = {
        "decision": "ignore",
        "rationale": "x" * 60,
        "alert_posted": False,
        "proposal_created": False,
    }
    monkeypatch.setenv("FAKE_CLAUDE_RESPONSE", json.dumps(response))
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {fake}")

    result = invoke_claude(signal_id=uuid4(), workspace=tmp_path)
    assert result.decision == "ignore"


def test_invoke_claude_raises_on_non_json_tail(monkeypatch, tmp_path):
    from trading_sandwich.triage.invocation import invoke_claude

    monkeypatch.setenv("CLAUDE_BIN", "echo just-a-plain-string-no-json")
    with pytest.raises(ValueError, match="could not parse"):
        invoke_claude(signal_id=uuid4(), workspace=tmp_path)


def test_invoke_claude_timeout(monkeypatch, tmp_path):
    from trading_sandwich.triage.invocation import InvocationTimeout, invoke_claude

    # python -c "import time; time.sleep(5)" will accept extra argv (-p ...)
    # without choking the way `sleep` does.
    monkeypatch.setenv(
        "CLAUDE_BIN",
        f"{sys.executable} -c 'import sys, time; time.sleep(5)'",
    )
    monkeypatch.setenv("CLAUDE_TIMEOUT_S", "1")
    with pytest.raises(InvocationTimeout):
        invoke_claude(signal_id=uuid4(), workspace=tmp_path)
