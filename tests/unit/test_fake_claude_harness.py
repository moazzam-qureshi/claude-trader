import json
import subprocess
import sys
from pathlib import Path


def test_fake_claude_emits_canned_json():
    script = Path("tests/fixtures/fake_claude.py").resolve()
    response = {
        "decision": "alert", "rationale": "x" * 60,
        "alert_posted": True, "proposal_created": False,
    }
    result = subprocess.run(
        [sys.executable, str(script), "triage", "abc"],
        env={"FAKE_CLAUDE_RESPONSE": json.dumps(response), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    last_line = result.stdout.strip().splitlines()[-1]
    parsed = json.loads(last_line)
    assert parsed["decision"] == "alert"
