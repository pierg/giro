"""Dispatch safety and cross-branch truth.

One lock per Spec makes double-dispatch impossible and a dead Run's lock
reclaimable. Status and target resolution read the tip of ``giro/<slug>``
wherever the human is standing — even from a checkout that never had the Spec's
files — and status grows a machine face for skills and CI.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from giro import cli
from giro.drivers import FakeDriver
from giro.runs import RunError, spec_lock
from giro.store import Store, StoreError
from giro.workspace import Workspace
from tests.conftest import git, good_worker, make_engine, spec_store


def full_run(project, **drivers):
    """A whole Spec loop: the branch ends up with the truth, the checkout stale."""
    engine = make_engine(
        project,
        worker=drivers.get("worker", FakeDriver([good_worker])),
        judge=drivers.get("judge", FakeDriver([{"verdict": "pass"}])),
    )
    return engine.implement("demo")


def status(project, *args):
    """Run `giro status`, returning (exit code, stdout)."""
    code = cli.main(["-C", str(project), "status", *args])
    return code


def test_a_second_run_against_a_locked_spec_is_refused_loudly(project):
    """The lock is held for the life of the Run: a dispatch arriving while a
    worker is mid-attempt is refused by name, never queued."""
    refused: list[str] = []

    def worker(prompt, cwd):
        rival = make_engine(project, worker=FakeDriver([good_worker]))
        with pytest.raises(RunError) as exc:
            rival.implement("demo")
        refused.append(str(exc.value))
        return good_worker(prompt, cwd)

    engine = make_engine(project, worker=FakeDriver([worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    assert f"pid {os.getpid()}" in refused[0]  # the live Run is named
    assert "demo/01-write-feature" in refused[0]  # ...along with what it is running
    assert "refused rather than queued" in refused[0]
    # ...and the Run it collided with finished untouched
    issue = spec_store(project).load_issues("demo")[0]
    assert issue.state == "done" and issue.attempts == 1


def test_the_lock_is_held_for_the_run_and_released_at_its_end(project):
    lock = Workspace(project).runtime_dir() / "locks" / "demo.json"
    held: list[dict] = []

    def worker(prompt, cwd):
        held.append(json.loads(lock.read_text()))
        return good_worker(prompt, cwd)

    engine = make_engine(project, worker=FakeDriver([worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    assert held[0]["pid"] == os.getpid() and held[0]["target"] == "demo/01-write-feature"
    assert not lock.exists()  # the next dispatch finds the Spec free


def test_a_dead_runs_lock_is_reclaimed_with_a_notice(project, capsys):
    """A killed Run leaves its lock behind; the next dispatch says so and proceeds."""
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    lock = Workspace(project).runtime_dir() / "locks" / "demo.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": dead.pid, "target": "demo", "started": "2026-01-01T00:00:00+00:00"})
    )

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    err = capsys.readouterr().err
    assert "stale lock" in err and str(dead.pid) in err
    assert not lock.exists()
    assert spec_store(project).load_issues("demo")[0].state == "done"


def test_an_unreadable_lock_is_a_corpse_not_a_claim(project, capsys):
    """A truncated or hand-mangled lock names no live Run, so it is reclaimed —
    never crashed on, never left blocking the Spec forever."""
    lock = Workspace(project).runtime_dir() / "locks" / "demo.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('{"pid": "not-a-pid"')

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"
    assert "stale lock" in capsys.readouterr().err


def test_a_spec_lock_names_its_holder_before_it_exists(project, monkeypatch):
    """Establishment is atomic: the holder is written, then the file is
    hardlinked into place, so the lock is never observable empty. Watch the
    link — its source already names this process the instant the lock appears.
    A create-then-write lock fails this: the file exists, briefly, holderless."""
    real_link = os.link
    seen: list[dict] = []

    def watched_link(src, dst):
        seen.append(json.loads(Path(src).read_text(encoding="utf-8")))
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", watched_link)
    runtime = Workspace(project).runtime_dir()
    with spec_lock(runtime, "demo", "demo"):
        pass
    assert seen and seen[0]["pid"] == os.getpid()


def test_racing_dispatches_never_hold_one_specs_lock_at_once(project):
    """The lock is whole the instant it exists, so dispatches racing at that
    instant yield one holder, never two. A create-then-write lock leaves an
    empty file mid-establishment; a rival reads no holder, judges the live lock
    a corpse, reclaims it, and both proceed on one Spec — exactly the
    double-dispatch the lock forbids. Sixteen threads contend at a barrier."""
    runtime = Workspace(project).runtime_dir()
    guard = threading.Lock()
    start = threading.Barrier(16)
    live = 0
    peak = 0
    acquired = 0

    def contend():
        nonlocal live, peak, acquired
        start.wait()
        try:
            with spec_lock(runtime, "demo", "demo"):
                with guard:
                    live += 1
                    peak = max(peak, live)
                    acquired += 1
                time.sleep(0.005)
                with guard:
                    live -= 1
        except RunError:
            pass

    threads = [threading.Thread(target=contend) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert acquired >= 1  # someone took it
    assert peak == 1  # ...but never two Runs on one Spec at the same instant


def test_status_prefers_the_branch_tip_over_the_stale_checkout_copy(project, capsys):
    assert full_run(project).outcome == "all-done"
    checkout_copy = (project / "docs" / "specs" / "demo" / "issues" / "01-write-feature.md")
    assert "state: ready" in checkout_copy.read_text()  # the checkout never moved

    assert status(project) == 0
    out = capsys.readouterr().out
    assert "demo [done]" in out and "(giro/demo +" in out
    assert "01-write-feature [done] attempts=1" in out


def test_status_reads_specs_the_checkout_does_not_have_and_drafts_it_does(project, capsys):
    """The human stands on a branch that never carried this Spec — and still sees
    it; a draft with no branch of its own is still read from under their feet."""
    assert full_run(project).outcome == "all-done"
    git(project, "checkout", "-b", "elsewhere")
    shutil.rmtree(project / "docs" / "specs" / "demo")
    Store(project).create_spec("later", "# Later feature\n\nNot started.\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "a branch that never knew demo")

    assert status(project) == 0
    out = capsys.readouterr().out
    assert "demo [done]" in out  # read from giro/demo
    assert "01-write-feature [done] attempts=1" in out
    assert "later [draft]" in out  # read from the checkout


def test_a_branch_forked_but_never_seeded_falls_back_to_the_checkout(project, capsys):
    """A Run that died before seeding leaves an empty branch — which is not truer
    than the checkout, so the Spec stays visible and keeps resolving."""
    git(project, "rm", "-r", "-q", "docs/specs/demo")
    git(project, "commit", "-q", "-m", "the base branch never carried this spec")
    git(project, "branch", "giro/demo", "main")  # forked, then the Run died
    Store(project).create_spec("demo", "# Demo feature\n\nStill here in the checkout.\n")

    assert status(project) == 0
    assert "demo [draft]" in capsys.readouterr().out

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    with pytest.raises(StoreError, match="missing from branch"):
        engine.implement("demo")  # resolved, then told plainly what is wrong


def test_implement_resolves_a_target_only_the_branch_knows(project):
    """A re-invoke from a stale checkout resumes the branch's real state instead
    of re-running work that is already done — or refusing to find it at all."""
    assert full_run(project).outcome == "all-done"
    git(project, "checkout", "-b", "elsewhere")
    shutil.rmtree(project / "docs" / "specs" / "demo")
    git(project, "add", "-A")
    git(project, "commit", "-m", "a branch that never knew demo")

    worker = FakeDriver([good_worker])
    engine = make_engine(project, worker=worker)
    spec_report = engine.implement("demo")
    assert spec_report.outcome == "done" and "already done" in spec_report.detail
    # ...and a bare issue id resolves through the branch too
    issue_report = engine.implement("01-write-feature")
    assert issue_report.outcome == "done" and issue_report.detail == "already done"
    assert worker.calls == []  # nothing was re-run


def test_a_stale_checkout_copy_never_reopens_finished_work(project):
    """The checkout still says `ready` — the branch says `done`, and the branch wins."""
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    worker = FakeDriver([good_worker])
    engine2 = make_engine(project, worker=worker)
    assert engine2.implement("demo/01-write-feature").detail == "already done"
    assert worker.calls == []


def test_machine_readable_status_carries_states_edges_and_branch_drift(project, capsys):
    store = Store(project)
    store.create_issue(
        "demo", "Second slice", "Create second.txt.", blocked_by=["01-write-feature"]
    )
    git(project, "add", "-A")
    git(project, "commit", "-m", "a blocked second slice")

    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"
    (project / "unrelated.txt").write_text("the human kept working\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "work on main while the machine ran")

    assert status(project, "--json") == 0
    report = json.loads(capsys.readouterr().out)
    spec = report["specs"][0]
    assert spec["slug"] == "demo" and spec["state"] == "draft"
    assert spec["source"] == "branch" and spec["branch"] == "giro/demo"
    assert spec["base_branch"] == "main"
    assert spec["ahead"] >= 3 and spec["behind"] == 1  # drift against the recorded base
    issues = {i["id"]: i for i in spec["issues"]}
    assert issues["01-write-feature"]["state"] == "done"
    assert issues["01-write-feature"]["attempts"] == 1
    assert issues["02-second-slice"]["state"] == "ready"
    assert issues["02-second-slice"]["blocked_by"] == ["01-write-feature"]
    assert issues["02-second-slice"]["ref"] == "demo/02-second-slice"
    assert report["needs_human"] == []


def test_machine_readable_status_reports_a_branchless_draft_plainly(project, capsys):
    assert status(project, "--json") == 0
    spec = json.loads(capsys.readouterr().out)["specs"][0]
    assert spec["state"] == "draft" and spec["source"] == "checkout"
    assert spec["branch"] is None and spec["base_branch"] is None
    assert spec["ahead"] is None and spec["behind"] is None


def test_status_exit_codes_are_unchanged_when_the_truth_is_on_the_branch(project, capsys):
    escalating = FakeDriver([{"outcome": "needs-human", "summary": "which format?"}])
    engine = make_engine(project, worker=escalating)
    assert engine.implement("demo/01-write-feature").outcome == "needs-human"
    # the checkout's copy still says `ready`: only the branch knows a human is needed
    assert "state: ready" in (
        project / "docs" / "specs" / "demo" / "issues" / "01-write-feature.md"
    ).read_text()

    assert status(project) == 2
    out = capsys.readouterr().out
    assert "needs-human — the loop is waiting on you" in out
    # the escalation points at the Spec's worktree — where that Issue's log actually is
    worktree = Workspace(project).spec_worktree_path("demo")
    assert f"{worktree}/docs/specs/demo/issues/01-write-feature.md" in out

    assert status(project, "--json") == 2
    report = json.loads(capsys.readouterr().out)
    assert report["needs_human"] == ["demo/01-write-feature"]
    assert report["specs"][0]["worktree"] == str(worktree)


def test_racing_reclaimers_of_a_stale_lock_yield_one_winner(project):
    """F3 — the reclaim critical section is atomic: two reclaimers racing on a
    stale (dead-pid) lock cannot both judge, unlink, and re-link. Exactly one
    wins the lock; the other raises RunError."""
    runtime = Workspace(project).runtime_dir()
    locks = runtime / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    # A pre-existing stale lock naming a pid that is guaranteed gone.
    stale = json.dumps({"pid": 99999999, "target": "demo", "started": "2026-01-01T00:00:00+00:00"})
    (locks / "demo.json").write_text(stale)

    guard = threading.Lock()
    live = 0
    peak = 0
    wins = 0
    losses = 0

    start = threading.Barrier(8)

    def contend():
        nonlocal live, peak, wins, losses
        start.wait()
        try:
            with spec_lock(runtime, "demo", "demo"):
                with guard:
                    live += 1
                    peak = max(peak, live)
                    wins += 1
                time.sleep(0.005)
                with guard:
                    live -= 1
        except RunError:
            with guard:
                losses += 1

    threads = [threading.Thread(target=contend) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert wins >= 1
    assert peak == 1  # never two reclaimers holding at once
    # Every thread either won or was refused — no crashes.
    assert wins + losses == 8
