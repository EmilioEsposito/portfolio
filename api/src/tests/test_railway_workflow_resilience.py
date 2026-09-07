"""Exercise the actual CI shell with failing HTTP/proxy responses, no network."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _environment_script() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/neon_workflow.yml").read_text())
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("id") == "get_railway_env":
                return step["run"]
    raise AssertionError("Railway environment lookup step missing")


@pytest.mark.parametrize("recover", [True, False])
def test_lookup_retries_bad_http_and_non_json_without_leaking_body(tmp_path, recover):
    script = _environment_script()
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    stub = tmp_path / "curl"
    stub.write_text("""#!/bin/bash
count=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$STATE_FILE"
if [ "$count" -eq 1 ]; then exit 22; fi
if [ "$count" -eq 2 ] || [ "$RECOVER" = "false" ]; then
  echo 'proxy secret-canary response'
else
  echo '{"data":{"environments":{"edges":[{"node":{"name":"portfolio-pr-17","id":"env-found"}}]}}}'
fi
""")
    stub.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/bash\nexit 0\n")
    sleep.chmod(0o755)
    output = tmp_path / "output"
    state = tmp_path / "count"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "STATE_FILE": str(state),
        "RECOVER": str(recover).lower(),
        "RAILWAY_API_TOKEN": "fake",
        "RAILWAY_PROJECT_ID": "project",
        "PR_NUMBER": "17",
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", "-eo", "pipefail"], input=script, text=True, capture_output=True, env=env
    )
    assert "secret-canary" not in result.stdout + result.stderr
    assert "parse error" not in result.stderr
    assert result.returncode == (0 if recover else 1)
    assert int(state.read_text()) == (3 if recover else 6)
    if recover:
        assert "env_id=env-found" in output.read_text()
