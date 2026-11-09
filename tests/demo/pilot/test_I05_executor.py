"""I05: the repair executor can authenticate with the subscription token and answers a one-turn request."""
import json
import os
import shlex
import subprocess

import pytest

from conftest import fake


def test_executor_token():
    if fake():
        pytest.skip("dry run: no executor")
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    assert tok, "CLAUDE_CODE_OAUTH_TOKEN not in the instance environment"
    cmd = os.environ.get("RH_EXECUTOR_CMD")
    assert cmd, "RH_EXECUTOR_CMD not set"
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY")}
    env["RH_EXECUTOR_MAX_TURNS"] = "1"
    r = subprocess.run(shlex.split(cmd), input="Reply with the single word OK. Do not use any tools.",
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, (r.stderr or r.stdout)[-500:]
    d = json.loads(r.stdout)
    assert not d.get("is_error"), d
    assert "OK" in str(d.get("result", ""))
