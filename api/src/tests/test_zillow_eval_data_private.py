"""The eval data folder holds applicants' emails: Git must never pick it up."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DATA = "api/src/sernia_ai/zillow_leads/evals/data"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


@pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(), reason="not a Git checkout"
)
class TestEvalDataStaysPrivate:
    def test_the_name_is_ignored_as_a_folder_or_a_symlink(self):
        assert _git("check-ignore", "-q", "--no-index", DATA).returncode == 0

    def test_files_inside_a_real_folder_are_ignored(self):
        if (REPO / DATA).is_symlink():
            pytest.skip("Git cannot track anything beyond a symlink")
        for path in (f"{DATA}/scenarios.jsonl", f"{DATA}/runs/a.jsonl"):
            assert _git("check-ignore", "-q", "--no-index", path).returncode == 0, path

    def test_nothing_under_it_is_tracked(self):
        assert _git("ls-files", "--", DATA).stdout == ""
