"""Worktree-resident Runs: the invoking checkout is sacred (ADR-0013).

Every loop runs in a persistent per-Spec worktree on ``giro/<slug>``, forked
from an explicit base branch recorded in Spec frontmatter. The checkout that
invoked the Run — any branch, clean or dirty — is never switched, never
gated, never written.
"""

import shutil

import pytest

from giro import cli
from giro.config import ConfigError
from giro.drivers import FakeDriver
from giro.loops import Report
from giro.store import Store
from giro.workspace import WorkspaceError
from tests.conftest import (
    MINIMAL_TOML,
    git,
    good_worker,
    make_engine,
    spec_store,
    spec_worktree,
)


def toml_with_base(branch: str) -> str:
    return MINIMAL_TOML.replace("concurrency = 1", f'concurrency = 1\nbase_branch = "{branch}"')


def toml_with_house_rule() -> str:
    """A project-local judge gate — a criterion no giro installation bundles."""
    return MINIMAL_TOML.replace(
        '  { name = "feature", type = "command", run = "test -f feature.txt" },',
        '  { name = "feature", type = "command", run = "test -f feature.txt" },\n'
        '  { name = "house", type = "judge", criterion = "house-rule" },',
    )


def house_rule(root):
    path = root / "prompts" / "house-rule.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# House rule\n\nThe change is neat.\n")
    return path


def snapshot(root):
    """Everything about a checkout a Run must leave byte-identical."""
    return (
        git(root, "branch", "--show-current"),
        git(root, "rev-parse", "HEAD"),
        git(root, "status", "--porcelain"),
        git(root, "diff"),
    )


def test_run_never_touches_the_invoking_checkout(project):
    git(project, "checkout", "-b", "side")
    (project / "scratch.txt").write_text("mine, uncommitted\n")
    (project / "README.md").write_text("# target\n\nmy own edit\n")
    before = snapshot(project)

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    assert snapshot(project) == before
    assert (project / "scratch.txt").read_text() == "mine, uncommitted\n"
    assert not (project / "feature.txt").exists()  # the work landed on the branch
    assert (spec_worktree(project) / "feature.txt").is_file()


def test_loop_commits_land_on_the_spec_branch_in_a_reused_worktree(project):
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    worktree = spec_worktree(project)
    assert (worktree / ".git").exists()
    branch_log = git(project, "log", "--oneline", "giro/demo")
    assert "claim" in branch_log and "attempt 1" in branch_log
    assert "attempt 1" not in git(project, "log", "--oneline", "main")
    assert spec_store(project).load_issues("demo")[0].state == "done"

    # a later Run reuses the same worktree rather than making a second one
    engine2 = make_engine(
        project, worker=FakeDriver([]), judge=FakeDriver([{"verdict": "pass"}])
    )
    assert engine2.implement("demo").outcome == "all-done"
    listed = git(project, "worktree", "list").strip().splitlines()
    assert len(listed) == 2 and str(worktree) in git(project, "worktree", "list")
    assert "state -> done" in git(project, "log", "--oneline", "giro/demo")


def test_a_deleted_worktree_is_recreated_without_reseeding(project):
    """The worktree is disposable; the branch is not. Recreating one resumes the
    Run's real state instead of starting the Spec over."""
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"
    shutil.rmtree(spec_worktree(project))

    engine2 = make_engine(
        project, worker=FakeDriver([]), judge=FakeDriver([{"verdict": "pass"}])
    )
    assert engine2.implement("demo").outcome == "all-done"
    assert (spec_worktree(project) / "feature.txt").is_file()  # earlier work is still there
    issue = spec_store(project).load_issues("demo")[0]
    assert issue.state == "done" and issue.attempts == 1  # not re-seeded, not re-run


def test_base_branch_is_recorded_once_and_reused(project):
    git(project, "checkout", "-b", "side")
    side_tip = git(project, "rev-parse", "HEAD").strip()
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    spec = spec_store(project).load_spec("demo")
    assert spec.base_branch == "side"  # the invoking branch at first activation
    assert spec.base == side_tip  # ...and the commit it forked from

    # a later Run from a different branch honours the recorded value
    git(project, "checkout", "main")
    engine2 = make_engine(
        project, worker=FakeDriver([]), judge=FakeDriver([{"verdict": "pass"}])
    )
    assert engine2.implement("demo").outcome == "all-done"
    reloaded = spec_store(project).load_spec("demo")
    assert reloaded.base_branch == "side" and reloaded.base == side_tip


def test_a_resume_reads_the_recorded_base_branch_not_the_checkout(project):
    """Once the branch exists there is nothing left to fork, so a later Run never
    asks where the human is standing — even standing nowhere."""
    git(project, "checkout", "-b", "side")
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    # the human moves on: the base branch is gone and the checkout is detached
    git(project, "checkout", "main")
    git(project, "branch", "-D", "side")
    git(project, "checkout", "--detach", "HEAD")
    assert git(project, "branch", "--show-current").strip() == ""

    engine2 = make_engine(
        project, worker=FakeDriver([]), judge=FakeDriver([{"verdict": "pass"}])
    )
    assert engine2.implement("demo").outcome == "all-done"
    assert spec_store(project).load_spec("demo").base_branch == "side"


def test_configured_base_branch_wins_over_the_invoking_branch(project):
    (project / "giro.toml").write_text(toml_with_base("main"))
    git(project, "add", "-A")
    git(project, "commit", "-m", "pin the base branch")
    main_tip = git(project, "rev-parse", "HEAD").strip()
    git(project, "checkout", "-b", "side")
    (project / "side-only.txt").write_text("x\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "side-only work")

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    spec = spec_store(project).load_spec("demo")
    assert spec.base_branch == "main" and spec.base == main_tip
    assert not (spec_worktree(project) / "side-only.txt").exists()


def test_spec_is_seeded_from_the_checkout_then_the_branch_copy_wins(project):
    spec_md = project / "docs" / "specs" / "demo" / "SPEC.md"
    spec_md.write_text(spec_md.read_text() + "\nSEED-MARKER\n")  # uncommitted

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"
    seeded = (spec_worktree(project) / "docs" / "specs" / "demo" / "SPEC.md").read_text()
    assert "SEED-MARKER" in seeded  # the checkout's copy, dirty and all

    # on resume the branch copy is the truth: a later checkout edit is ignored
    spec_md.write_text(spec_md.read_text() + "\nLATER-MARKER\n")
    engine2 = make_engine(
        project, worker=FakeDriver([]), judge=FakeDriver([{"verdict": "pass"}])
    )
    assert engine2.implement("demo").outcome == "all-done"
    resumed = (spec_worktree(project) / "docs" / "specs" / "demo" / "SPEC.md").read_text()
    assert "SEED-MARKER" in resumed and "LATER-MARKER" not in resumed


def test_runtime_directory_is_never_committed(project):
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    assert ".giro" not in git(project, "status", "--porcelain")
    assert ".giro" not in git(project, "ls-files")
    assert ".giro" not in git(project, "ls-tree", "-r", "--name-only", "giro/demo")
    # not even a human running `git add -A` in the checkout can stage it
    git(project, "add", "-A")
    assert ".giro" not in git(project, "diff", "--cached", "--name-only")


def test_parallel_waves_run_from_the_spec_worktree(project):
    (project / "giro.toml").write_text(MINIMAL_TOML.replace("concurrency = 1", "concurrency = 2"))
    store = Store(project)
    store.create_issue("demo", "Second slice", "Create second.txt.")
    git(project, "add", "-A")
    git(project, "commit", "-m", "two parallel slices")
    git(project, "checkout", "-b", "side")
    (project / "scratch.txt").write_text("mine\n")
    before = snapshot(project)

    def worker(prompt, cwd):
        (cwd / "feature.txt").write_text("ok\n")  # the [verify] gate wants this
        name = "second.txt" if "Second slice" in prompt else "first.txt"
        (cwd / name).write_text("ok\n")
        return {"outcome": "completed", "summary": f"wrote {name}"}

    engine = make_engine(
        project,
        worker=FakeDriver([worker, worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    assert engine.implement("demo").outcome == "all-done"

    assert snapshot(project) == before
    worktree = spec_worktree(project)
    assert (worktree / "first.txt").is_file() and (worktree / "second.txt").is_file()
    # the wave's own worktrees are gone; the Spec's persistent one remains
    assert len(git(project, "worktree", "list").strip().splitlines()) == 2


def test_missing_base_branch_fails_before_any_context_is_spawned(project):
    (project / "giro.toml").write_text(toml_with_base("nope"))
    worker = FakeDriver([good_worker])
    engine = make_engine(project, worker=worker)

    with pytest.raises(WorkspaceError, match="nope"):
        engine.implement("demo")
    assert worker.calls == []
    assert not spec_worktree(project).exists()


def test_checkout_sitting_on_the_spec_branch_fails_loudly(project):
    """Two working trees can never hold one branch — say so, rather than letting
    git's own message surface after the human has already dispatched."""
    git(project, "checkout", "-b", "giro/demo")
    worker = FakeDriver([good_worker])
    engine = make_engine(project, worker=worker)

    with pytest.raises(WorkspaceError, match="is checked out"):
        engine.implement("demo")

    # ...and pinning a base branch does not help: the worktree still wants giro/demo
    (project / "giro.toml").write_text(toml_with_base("main"))
    engine2 = make_engine(project, worker=worker)
    with pytest.raises(WorkspaceError, match="is checked out"):
        engine2.implement("demo")
    assert worker.calls == []


def test_a_spec_branch_cannot_be_its_own_base(project):
    (project / "giro.toml").write_text(toml_with_base("giro/demo"))
    worker = FakeDriver([good_worker])
    engine = make_engine(project, worker=worker)

    with pytest.raises(WorkspaceError, match="cannot fork from itself"):
        engine.implement("demo")
    assert worker.calls == []
    assert not spec_worktree(project).exists()


def test_repository_without_commits_fails_clearly(tmp_path):
    root = tmp_path / "unborn"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "giro-test")
    (root / "giro.toml").write_text(MINIMAL_TOML)
    spec_dir = root / "docs" / "specs" / "demo"
    spec_dir.mkdir(parents=True)
    (spec_dir / "SPEC.md").write_text("---\nstate: draft\n---\n# Demo\n\nBody.\n")

    worker = FakeDriver([good_worker])
    engine = make_engine(root, worker=worker)
    with pytest.raises(WorkspaceError, match="no commits"):
        engine.implement("demo")
    assert worker.calls == []


def test_preflight_judges_the_tree_the_gates_will_run_in(project):
    """A criterion that exists only as an uncommitted file in the checkout is not
    there when the gate runs — so it must fail closed at zero cost, not mid-Run."""
    (project / "giro.toml").write_text(toml_with_house_rule())
    house_rule(project)  # uncommitted: the Spec branch will never see it
    worker = FakeDriver([good_worker])
    engine = make_engine(project, worker=worker)

    with pytest.raises(ConfigError, match="house-rule"):
        engine.implement("demo/01-write-feature")
    assert worker.calls == []


def test_a_gate_criterion_on_the_branch_alone_is_enough(project):
    """The mirror image: committed content is what the gates read, so a criterion
    the human has since deleted from their own tree still resolves."""
    (project / "giro.toml").write_text(toml_with_house_rule())
    criterion = house_rule(project)
    git(project, "add", "-A")
    git(project, "commit", "-m", "a house rule of our own")
    criterion.unlink()  # gone from the checkout, still on the branch

    judge = FakeDriver([{"verdict": "pass"}])
    engine = make_engine(project, worker=FakeDriver([good_worker]), judge=judge)
    assert engine.implement("demo/01-write-feature").outcome == "done"
    assert "The change is neat." in judge.calls[0]["prompt"]


def test_a_dirty_spec_worktree_refuses_to_run(project):
    """The clean-tree requirement moved with the loop: it guards the engine's
    own worktree now, never the human's checkout."""
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"
    (spec_worktree(project) / "leftover.txt").write_text("half-written\n")

    engine2 = make_engine(project, worker=FakeDriver([good_worker]))
    with pytest.raises(WorkspaceError, match="leftover.txt"):
        engine2.implement("demo")


def test_foreground_exit_codes_are_unchanged(project, monkeypatch):
    """0 proof, 2 needs-human, 1 error — the contract CI reads."""

    class StubEngine:
        def __init__(self, result):
            self.result = result

        def implement(self, target, run_id="", quiet=False):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    def stub(result):
        monkeypatch.setattr(cli, "_build_engine", lambda root: StubEngine(result))

    stub(Report("all-done", "demo", "validate passed"))
    assert cli.main(["-C", str(project), "implement", "demo"]) == 0
    stub(Report("needs-human", "demo", "unfinished issue(s)"))
    assert cli.main(["-C", str(project), "implement", "demo"]) == 2
    stub(WorkspaceError("base branch 'nope' does not exist"))
    assert cli.main(["-C", str(project), "implement", "demo"]) == 1
