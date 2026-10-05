"""``giro doctor`` — the one-look readiness report.

Five sections (config, drivers, git, specs, projection), one exit code: 0 when
every "must" check passes, 1 otherwise. The "must" set is the checks the engine
itself refuses to run without — config parses, every roster driver is on PATH,
the repo has commits, HEAD is on a branch, and every Spec/Issue on disk parses
with resolvable edges — so a green doctor means a Run would not die on any of
them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from giro import cli

MINIMAL_TOML = """\
[verify]
gates = [ { name = "t", type = "command", run = "true" } ]

[validate]
gates = [ { name = "v", type = "judge", rubric = "ok" } ]
"""


def _git(root: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def _seeded_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "giro-test")
    (root / "giro.toml").write_text(MINIMAL_TOML)
    (root / "README.md").write_text("# repo\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed")
    return root


def test_doctor_happy_path_exits_zero(tmp_path, monkeypatch, capsys):
    root = _seeded_repo(tmp_path)
    # Every subprocess driver's CLI is 'there' (a path is returned).
    monkeypatch.setattr(
        "giro.cli.shutil.which",
        lambda name: f"/usr/local/bin/{name}",
    )

    assert cli.main(["-C", str(root), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "ok" in out and "giro.toml" in out
    assert "worker driver" in out and "claude" in out
    assert "git repo" in out


def test_doctor_missing_driver_fails(tmp_path, monkeypatch):
    root = _seeded_repo(tmp_path)
    # No driver CLI is on PATH — every role's driver check fails.
    monkeypatch.setattr("giro.cli.shutil.which", lambda name: None)

    assert cli.main(["-C", str(root), "doctor"]) == 1


def test_doctor_json_reports_every_section(tmp_path, monkeypatch, capsys):
    root = _seeded_repo(tmp_path)
    monkeypatch.setattr(
        "giro.cli.shutil.which", lambda name: f"/usr/local/bin/{name}"
    )

    assert cli.main(["-C", str(root), "doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"config", "drivers", "git", "specs", "projection"}
    for section in payload.values():
        assert isinstance(section, list)
        for entry in section:
            assert set(entry) == {"name", "ok", "detail"}


def test_doctor_broken_config_reports_and_fails(tmp_path, monkeypatch, capsys):
    root = _seeded_repo(tmp_path)
    (root / "giro.toml").write_text("this is not valid TOML: [[[")
    monkeypatch.setattr(
        "giro.cli.shutil.which", lambda name: f"/usr/local/bin/{name}"
    )
    assert cli.main(["-C", str(root), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "giro.toml" in out


def test_doctor_no_git_repo_fails(tmp_path, monkeypatch, capsys):
    root = tmp_path / "not-a-repo"
    root.mkdir()
    (root / "giro.toml").write_text(MINIMAL_TOML)
    monkeypatch.setattr(
        "giro.cli.shutil.which", lambda name: f"/usr/local/bin/{name}"
    )
    assert cli.main(["-C", str(root), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "not a git repository" in out


def test_doctor_detached_head_fails(tmp_path, monkeypatch, capsys):
    root = _seeded_repo(tmp_path)
    # Detach HEAD onto the current commit.
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    _git(root, "checkout", "--detach", head)
    monkeypatch.setattr(
        "giro.cli.shutil.which", lambda name: f"/usr/local/bin/{name}"
    )
    assert cli.main(["-C", str(root), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "detached HEAD" in out
