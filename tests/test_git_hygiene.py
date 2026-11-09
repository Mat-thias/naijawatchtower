"""Real data must never be tracked by git (CLAUDE.md rule 2)."""

import subprocess

from naijawatchtower.config import ROOT


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def tracked(prefix: str) -> list[str]:
    return [p for p in git("ls-files", "--cached", prefix).stdout.splitlines() if p]


def test_data_real_is_never_tracked():
    assert tracked("data_real") == []
    assert git("check-ignore", "-q", "data_real/images/example.jpg").returncode == 0


def test_data_redacted_untracked_until_organisers_confirm():
    """While `data_redacted/` is in .gitignore (D3 open), nothing in it may be tracked."""
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    if "data_redacted/" in gitignore:
        assert tracked("data_redacted") == []
        assert git("check-ignore", "-q", "data_redacted/R001.jpg").returncode == 0
