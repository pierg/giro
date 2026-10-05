"""Parallel wave tests: isolated worktrees, serialized integration.

Workers are routed by prompt content (never by call order) so every test is
deterministic regardless of thread scheduling.
"""

import subprocess

from giro.drivers import FakeDriver
from giro.store import Store
from tests.conftest import git, make_engine, spec_store, spec_worktree

PARALLEL_TOML = """\
[verify]
gates = [
  {{ name = "gate", type = "command", run = "{gate}" }},
]

[validate]
gates = [
  {{ name = "spec-fit", type = "judge", rubric = "Everything the spec asks for exists." }},
]

[runner]
concurrency = 2

[budget]
issue_attempts = {attempts}
validate_cycles = 1
gate_timeout = 60
"""


def parallel_project(project, *, gate="true", attempts=3, issues=()):
    """Reshape the seeded project: custom gate, concurrency 2, custom issues.

    Returns the store to read results from — the Spec's copy on its branch,
    which is where the loop's commits land."""
    store = Store(project)
    store.load_issues("demo")[0].path.unlink()
    (project / "giro.toml").write_text(PARALLEL_TOML.format(gate=gate, attempts=attempts))
    for title, body, blocked_by in issues:
        store.create_issue("demo", title, body, blocked_by=list(blocked_by))
    git(project, "add", "-A")
    git(project, "commit", "-m", "parallel setup")
    return spec_store(project)


def route_by_title(routes):
    """A worker callable that acts based on which Issue is in the prompt."""

    def worker(prompt, cwd):
        for needle, action in routes.items():
            if needle in prompt:
                action(cwd)
                return {"outcome": "completed", "summary": f"did {needle}"}
        raise AssertionError(f"no route matched prompt: {prompt[:200]}")

    return worker


def worktree_count(root) -> int:
    out = subprocess.run(
        ["git", "worktree", "list"], cwd=root, capture_output=True, text=True
    ).stdout
    return len(out.strip().splitlines())


def test_parallel_wave_both_done_with_integrated_verify(project, tmp_path):
    counter = tmp_path / "gate-runs"
    store = parallel_project(
        project,
        gate=f"echo x >> {counter}",
        issues=[
            ("Alpha slice", "Create alpha.txt.", ()),
            ("Beta slice", "Create beta.txt.", ()),
        ],
    )
    worker = route_by_title(
        {
            "Alpha": lambda cwd: (cwd / "alpha.txt").write_text("a\n"),
            "Beta": lambda cwd: (cwd / "beta.txt").write_text("b\n"),
        }
    )
    engine = make_engine(
        project,
        worker=FakeDriver([worker, worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    report = engine.implement("demo")

    assert report.outcome == "all-done"
    worktree = spec_worktree(project)
    assert (worktree / "alpha.txt").is_file() and (worktree / "beta.txt").is_file()
    # verify ran twice branch-local (one per worktree) and twice integrated
    assert len(counter.read_text().splitlines()) == 4
    merges = git(project, "log", "--merges", "--oneline", "giro/demo")
    assert len(merges.strip().splitlines()) == 2
    for issue in store.load_issues("demo"):
        assert issue.state == "done" and issue.attempts == 1
        assert "Integrated — merged and verified" in issue.body
    assert worktree_count(project) == 2  # the wave's worktrees are gone; the Spec's remains


def test_parallel_conflict_retries_from_new_head(project):
    store = parallel_project(
        project,
        issues=[
            ("Alpha slice", "Write shared.txt.", ()),
            ("Beta slice", "Write shared.txt.", ()),
        ],
    )
    worker = route_by_title(
        {
            "Alpha": lambda cwd: (cwd / "shared.txt").write_text("alpha\n"),
            "Beta": lambda cwd: (cwd / "shared.txt").write_text("beta\n"),
        }
    )
    engine = make_engine(
        project,
        worker=FakeDriver([worker, worker, worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    report = engine.implement("demo")

    assert report.outcome == "all-done"
    issues = store.load_issues("demo")
    alpha, beta = issues
    assert alpha.state == "done" and alpha.attempts == 1
    assert beta.state == "done" and beta.attempts == 2  # conflicted, then retried
    assert "Merge conflict — retrying from the updated integration HEAD" in beta.body
    # the retry ran on the updated HEAD and its content won
    assert (spec_worktree(project) / "shared.txt").read_text() == "beta\n"
    assert worktree_count(project) == 2  # only the Spec's persistent worktree remains


def test_integrated_verify_failure_escalates_and_resets(project):
    # each worker's marker passes branch-local; two markers together fail the
    # integrated run — pass in isolation, fail merged.
    store = parallel_project(
        project,
        gate="test $(ls marker-* 2>/dev/null | wc -l) -lt 2",
        attempts=1,
        issues=[
            ("Alpha slice", "Write marker-alpha.", ()),
            ("Beta slice", "Write marker-beta.", ()),
        ],
    )
    worker = route_by_title(
        {
            "Alpha": lambda cwd: (cwd / "marker-alpha").write_text("a\n"),
            "Beta": lambda cwd: (cwd / "marker-beta").write_text("b\n"),
        }
    )
    engine = make_engine(
        project,
        worker=FakeDriver([worker, worker]),
        judge=FakeDriver([]),
    )
    report = engine.implement("demo")

    assert report.outcome == "needs-human"
    alpha, beta = store.load_issues("demo")
    assert alpha.state == "done"
    assert beta.state == "needs-human"  # budget 1: no retry after integrated failure
    assert "gate: `test" in beta.body or "Budget exhausted" in beta.body
    # the failed merge was reset: only alpha's marker is on the integration branch
    worktree = spec_worktree(project)
    assert (worktree / "marker-alpha").is_file()
    assert not (worktree / "marker-beta").exists()
    assert worktree_count(project) == 2  # only the Spec's persistent worktree remains


def test_stale_in_progress_is_reclaimed(project):
    """A crashed run leaves a committed in-progress claim; the engine is the
    only writer, so the next run safely re-claims it."""
    store = Store(project)
    issue = store.load_issues("demo")[0]
    issue.state = "in-progress"
    store.save_issue(issue)
    git(project, "add", "-A")
    git(project, "commit", "-m", "simulate crash mid-claim")

    from tests.conftest import good_worker

    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    report = engine.implement("demo")
    assert report.outcome == "all-done"
    assert spec_store(project).load_issues("demo")[0].state == "done"


def test_wave_worktrees_live_under_dot_giro_and_leftovers_are_cleaned(project, tmp_path):
    """F4 — wave worktrees live under .giro/ (git-ignored, deterministic paths)
    so a hard-killed run's debris is cleaned up on the next wave rather than
    wedging in the system tmp dir forever."""
    store = parallel_project(
        project,
        issues=[
            ("Alpha slice", "Create alpha.txt.", ()),
            ("Beta slice", "Create beta.txt.", ()),
        ],
    )
    # Simulate a killed prior wave: a leftover directory at the deterministic
    # wave-worktree path, with content, and no git worktree registration for it.
    # The runtime dir is the invoking checkout's .giro — the engine's workshop.
    leftover_root = project / ".giro" / "wave-worktrees" / "demo"
    (leftover_root / "01-alpha-slice").mkdir(parents=True, exist_ok=True)
    (leftover_root / "01-alpha-slice" / "stale.txt").write_text("from a dead run\n")

    worker = route_by_title(
        {
            "Alpha": lambda cwd: (cwd / "alpha.txt").write_text("a\n"),
            "Beta": lambda cwd: (cwd / "beta.txt").write_text("b\n"),
        }
    )
    engine = make_engine(
        project,
        worker=FakeDriver([worker, worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    report = engine.implement("demo")

    assert report.outcome == "all-done"
    for issue in store.load_issues("demo"):
        assert issue.state == "done"
    # the wave-worktrees parent still exists (git-ignored), but the per-issue
    # directories were removed after the wave; leftover stale content is gone.
    assert leftover_root.is_dir()
    assert not (leftover_root / "01-alpha-slice").exists()


def test_wave_thread_crash_does_not_sink_siblings(project):
    """F7 — a non-DriverError exception in one wave thread must not delete the
    other threads' green worker branches. The sibling Issue integrates as
    done; the crashing Issue is failed with attempts += 1 and findings."""
    store = parallel_project(
        project,
        attempts=1,
        issues=[
            ("Alpha slice", "Create alpha.txt.", ()),
            ("Beta slice", "Create beta.txt.", ()),
        ],
    )

    def crash_or_do(prompt, cwd):
        if "Alpha" in prompt:
            raise RuntimeError("wave thread died unexpectedly")
        (cwd / "beta.txt").write_text("b\n")
        return {"outcome": "completed", "summary": "did beta"}

    engine = make_engine(
        project,
        worker=FakeDriver([crash_or_do, crash_or_do]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    report = engine.implement("demo")

    # The Spec is needs-human (Alpha crashed), but Beta was integrated.
    assert report.outcome == "needs-human"
    issues = {i.id: i for i in store.load_issues("demo")}
    alpha = issues["01-alpha-slice"]
    beta = issues["02-beta-slice"]
    assert alpha.state == "needs-human"
    assert alpha.attempts == 1
    assert "wave thread errored" in alpha.body
    # Beta was integrated normally — its work landed on the branch and the file
    # is on the integration worktree.
    assert beta.state == "done"
    worktree = spec_worktree(project)
    assert (worktree / "beta.txt").is_file()
