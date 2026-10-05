"""The global worker cap: one ceiling across every Run.

Dispatching freely must not melt the machine or the budget, so worker contexts
share one configurable ceiling however many Runs are in flight. A Run that
arrives at a full house waits its turn — it is never refused, and it never
exceeds the cap.
"""

import json
import os
import threading
import time

from giro import runs
from giro.drivers import FakeDriver
from giro.runs import worker_slot
from giro.store import Store
from tests.conftest import MINIMAL_TOML, git, make_engine, spec_store


def toml_with_cap(workers: int) -> str:
    return MINIMAL_TOML.replace("concurrency = 1", f"concurrency = 1\nmax_workers = {workers}")


def two_specs(project, workers: int):
    """Two independent Specs, each one Issue, under a given worker ceiling."""
    (project / "giro.toml").write_text(toml_with_cap(workers))
    store = Store(project)
    store.create_spec("other", "# Other feature\n\nThe repo contains feature.txt saying ok.\n")
    store.create_issue("other", "Write feature", "Create feature.txt.")
    git(project, "add", "-A")
    git(project, "commit", "-m", "a second spec to run alongside")
    return ["demo/01-write-feature", "other/01-write-feature"]


def run_both(project, targets, worker):
    """Dispatch both Specs at once, in this process; return their reports."""
    reports: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(target):
        try:
            engine = make_engine(project, worker=FakeDriver([worker]))
            reports[target] = engine.implement(target)
        except BaseException as exc:  # surfaced by the caller, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(t,)) for t in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    return reports


def test_the_cap_holds_across_two_simultaneous_runs(project):
    """Two Runs, one slot: both finish, and never a second worker at once."""
    targets = two_specs(project, workers=1)
    lock = threading.Lock()
    live = 0
    peak = 0

    def worker(prompt, cwd):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            threading.Event().wait(0.3)  # hold the slot long enough to collide
            (cwd / "feature.txt").write_text("ok\n")
            return {"outcome": "completed", "summary": "wrote feature.txt"}
        finally:
            with lock:
                live -= 1

    reports = run_both(project, targets, worker)

    assert peak == 1  # the second Run waited for a slot instead of exceeding the cap
    assert [reports[t].outcome for t in targets] == ["done", "done"]  # ...and neither was refused
    for slug in ("demo", "other"):
        assert spec_store(project, slug).load_issues(slug)[0].state == "done"


def test_the_cap_is_configurable(project):
    """Raise the ceiling and the same two Runs really do overlap — the barrier
    only clears if two workers are in flight together."""
    targets = two_specs(project, workers=2)
    both_in_flight = threading.Barrier(2, timeout=30)

    def worker(prompt, cwd):
        both_in_flight.wait()  # BrokenBarrierError if the cap serialized them
        (cwd / "feature.txt").write_text("ok\n")
        return {"outcome": "completed", "summary": "wrote feature.txt"}

    reports = run_both(project, targets, worker)
    assert [reports[t].outcome for t in targets] == ["done", "done"]


def test_a_slot_left_by_a_dead_run_is_reclaimed(project):
    """A killed Run cannot cost the machine a slot forever: a slot whose holder
    is gone is free, like a stale lock."""
    slots = (project / ".giro" / "slots")
    slots.mkdir(parents=True)
    (slots / "01.json").write_text('{"pid": 999999999, "taken": "2026-01-01T00:00:00+00:00"}')
    (project / "giro.toml").write_text(toml_with_cap(1))
    git(project, "add", "-A")
    git(project, "commit", "-m", "one worker at a time")

    def worker(prompt, cwd):
        (cwd / "feature.txt").write_text("ok\n")
        return {"outcome": "completed", "summary": "wrote feature.txt"}

    engine = make_engine(project, worker=FakeDriver([worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"
    assert not (slots / "01.json").exists()  # taken, then given back


def test_a_slot_with_no_readable_holder_is_not_free(tmp_path, monkeypatch):
    """A slot file that names nobody is damage, not a take in flight — and a
    context that read it as free would sit in a slot someone else is holding,
    which is the cap itself."""
    monkeypatch.setattr(runs, "SLOT_POLL", 0.01)
    slots = tmp_path / "slots"
    slots.mkdir(parents=True)
    unreadable = slots / "01.json"
    unreadable.write_text("")  # no holder to prove dead
    took = threading.Event()

    def take():
        with worker_slot(tmp_path, 1):
            took.set()

    waiter = threading.Thread(target=take, daemon=True)
    waiter.start()
    assert not took.wait(0.3)  # the one slot is held until proven otherwise
    unreadable.unlink()  # ...and the waiter takes its turn once it is gone
    assert took.wait(5)
    waiter.join(timeout=5)


def test_a_corpse_is_never_reclaimed_out_from_under_a_live_take(tmp_path, monkeypatch):
    """A corpse is one slot, not one per taker.

    Killing a detached Run leaves a slot behind, so two contexts routinely
    arrive at the same corpse together. Each judges it dead — truthfully, at the
    moment it looks — and reclaiming is how a slot is taken, so a reclaim that is
    not exclusive with the take that follows it lets the slower taker remove what
    the faster one has just sat down in. Both would then believe they held the
    cap's only slot: the cap exceeded by the very move that protects it.

    So the second taker is made to stall between judging the corpse and acting on
    it, and a first taker seats itself in that gap. The corpse it saw is stale by
    the time it moves, and it must not act on what it saw.
    """
    monkeypatch.setattr(runs, "SLOT_POLL", 0.01)
    slots = tmp_path / "slots"
    slots.mkdir(parents=True)
    (slots / "01.json").write_text('{"pid": 999999999, "taken": "2026-01-01T00:00:00+00:00"}')

    judged = runs._is_corpse
    saw_the_corpse = threading.Event()
    sat_down = threading.Event()
    lock = threading.Lock()
    live = 0
    peak = 0
    stolen: list[str] = []

    def dawdling(path):
        """Judge honestly, then stall once — long enough for another taker to sit
        down in this very slot before this one acts on what it saw. A taker that
        cannot get in never sets the event, so the stall is bounded."""
        verdict = judged(path)
        if not saw_the_corpse.is_set():
            saw_the_corpse.set()
            sat_down.wait(0.5)
        return verdict

    monkeypatch.setattr(runs, "_is_corpse", dawdling)

    def take(announce: bool) -> None:
        nonlocal live, peak
        with worker_slot(tmp_path, 1) as slot:
            mine = slot.stat().st_ino
            with lock:
                live += 1
                peak = max(peak, live)
            if announce:
                sat_down.set()
            time.sleep(0.1)
            with lock:
                live -= 1
            if not slot.exists() or slot.stat().st_ino != mine:
                stolen.append(slot.name)

    dawdler = threading.Thread(target=take, args=(False,), daemon=True)
    dawdler.start()
    assert saw_the_corpse.wait(5)  # the corpse is judged dead, and not yet gone
    take(announce=True)  # ...and this taker sits down in the gap
    dawdler.join(timeout=10)
    assert not dawdler.is_alive()  # the stalled taker still got its turn

    assert stolen == []  # a take is only ever removed by its own holder...
    assert peak == 1  # ...so one of them sits, and the other waits its turn
    assert not (slots / "01.json").exists()  # taken, then given back


def test_a_slot_never_exists_without_its_holder(tmp_path):
    """Whatever a scanning context reads, it reads a whole take: the slot file
    appears already naming the process that owns it."""
    with worker_slot(tmp_path, 1) as slot:
        assert json.loads(slot.read_text())["pid"] == os.getpid()
        assert [p.name for p in (tmp_path / "slots").iterdir()] == ["01.json"]
