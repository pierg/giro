"""Workspace git helpers — the material `giro verify` judges, and diff scoping."""

from giro.workspace import Workspace
from tests.conftest import git


def test_working_tree_diff_reports_tracked_and_untracked(project):
    ws = Workspace(project)
    assert "clean" in ws.working_tree_diff()  # nothing staged or dropped yet

    (project / "README.md").write_text("# target\n\nchanged line\n")  # tracked edit
    (project / "brand-new.txt").write_text("hello\n")  # untracked
    material = ws.working_tree_diff()
    assert "changed line" in material  # tracked diff is present
    assert "brand-new.txt" in material  # untracked file is listed


def test_diff_since_scopes_to_a_base(project):
    ws = Workspace(project)
    base = ws.head_sha()
    (project / "added.txt").write_text("x\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "add a file")
    diff = ws.diff_since(base)
    assert "added.txt" in diff


def test_engine_commits_skip_project_pre_commit_hook(project):
    """F9 — project pre-commit hooks are project judgment, not engine judgment.
    Engine bookkeeping commits pass ``--no-verify`` so a failing hook does not
    wedge the loop."""
    hook_dir = project / ".git" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook = hook_dir / "pre-commit"
    hook.write_text("#!/bin/sh\necho blocked by hook\nexit 1\n")
    hook.chmod(0o755)

    ws = Workspace(project)
    (project / "flag.txt").write_text("hi\n")
    # Without --no-verify the hook would fail this call.
    assert ws.commit_all("engine bookkeeping") is True
    assert "blocked by hook" not in git(project, "log", "-1", "--format=%s")


def test_engine_commits_do_not_gpg_sign(project):
    """F9 — the engine's git calls disable gpg signing so an interactive
    pinentry cannot hang the loop, regardless of user or repo config."""
    from giro.workspace import Workspace as WS

    # Even if the user has commit.gpgsign=true in their global config, engine
    # commits must NOT be signed (the engine passes -c commit.gpgsign=false).
    ws = WS(project)
    (project / "signed-test.txt").write_text("hi\n")
    assert ws.commit_all("engine bookkeeping") is True
    # No signature on the resulting commit.
    signature = git(project, "log", "-1", "--format=%G?").strip()
    # 'G' = good sig, 'N' = no signature. Engine commits must be 'N'.
    assert signature == "N"
