"""The Ledger: liveness, the event stream, and the outcome.

Every Run — foreground or dispatched — narrates itself: one event per
state-changing moment, a record of where it is, and a last word. It is
observability only, so a Run whose Ledger is deleted loses nothing durable.
"""

import itertools
import json
import os
import shutil
import threading

from giro import cli
from giro.drivers import FakeDriver
from giro.ledger import EVENT_TYPES, Ledger, list_runs, read_events
from giro.store import Store
from giro.workspace import Workspace
from tests.conftest import MINIMAL_TOML, git, good_worker, make_engine, spec_store

PARALLEL_TOML = MINIMAL_TOML.replace("concurrency = 1", "concurrency = 2")


def runtime(project):
    return Workspace(project).runtime_path()


def latest(project):
    """The Run the project last recorded."""
    return list_runs(runtime(project))[0]


def events(project, kind=""):
    story = read_events(latest(project).dir)
    return [e for e in story if e["type"] == kind] if kind else story


def types(project):
    return [e["type"] for e in events(project)]


def slicing_worker():
    """A worker that always leaves something new behind, so a second Issue is
    never mistaken for an empty attempt."""
    counter = itertools.count(1)

    def worker(prompt, cwd):
        (cwd / "feature.txt").write_text("ok\n")  # what the [verify] gate wants
        n = next(counter)
        (cwd / f"slice-{n}.txt").write_text("ok\n")
        return {"outcome": "completed", "summary": f"wrote slice {n}"}

    return worker


def test_a_run_records_every_state_changing_moment(project):
    """One Spec loop, start to finish: activation, plan, wave, claim, attempt,
    gap cycle, second wave, exit — one event each, in the order they happened."""
    Store(project).load_issues("demo")[0].path.unlink()  # unplanned: the planner slices it
    git(project, "add", "-A")
    git(project, "commit", "-m", "an unplanned spec")

    planner = FakeDriver([{"issues": [{"title": "Write feature", "body": "Create feature.txt."}]}])
    judge = FakeDriver(
        [
            {"verdict": "fail", "findings": [{"summary": "the second slice is missing"}]},
            {"verdict": "pass"},
        ]
    )
    engine = make_engine(
        project, worker=FakeDriver([slicing_worker()] * 2), judge=judge, planner=planner
    )
    assert engine.implement("demo").outcome == "all-done"

    assert set(types(project)) <= set(EVENT_TYPES)  # the stream's shape is a closed set
    assert types(project) == [
        "activation",
        "plan",
        "wave",
        "claim",
        "attempt",
        "gap-cycle",
        "wave",
        "claim",
        "attempt",
        "exit",
    ]
    activation = events(project, "activation")[0]
    assert activation["spec"] == "demo" and activation["branch"] == "giro/demo"
    assert activation["base_branch"] == "main"
    assert events(project, "plan")[0]["issues"] == ["01-write-feature"]
    first, second = events(project, "wave")
    assert (first["wave"], first["issues"]) == (1, ["01-write-feature"])
    assert second["wave"] == 2  # waves keep counting across the gap cycle
    gap = events(project, "gap-cycle")[0]
    assert gap["cycle"] == 1 and gap["issues"] == second["issues"]
    assert gap["findings"][0]["summary"] == "the second slice is missing"
    assert events(project, "claim")[0]["issue"] == "demo/01-write-feature"

    exit_event = events(project, "exit")[0]
    run = latest(project)
    assert (exit_event["outcome"], run.outcome) == ("all-done", "all-done")
    finished = "validate passed — merge branch giro/demo when ready"
    assert run.detail == exit_event["detail"] == finished
    assert run.liveness == "finished" and run.spec == "demo"
    assert [e["seq"] for e in events(project)] == list(range(1, 11))


def test_attempt_events_carry_the_outcome_verdicts_and_findings(project):
    """A failed attempt's story is which gate said no, and why — not just that
    something did."""
    lazy_then_good = FakeDriver(
        [{"outcome": "completed", "summary": "claims done"}, good_worker]
    )
    engine = make_engine(project, worker=lazy_then_good)
    assert engine.implement("demo/01-write-feature").outcome == "done"

    first, second = events(project, "attempt")
    assert first["n"] == 1 and first["outcome"] == "no-changes"
    assert "no changes" in first["findings"][0]["summary"]
    assert second["n"] == 2 and second["outcome"] == "green"
    assert second["verdicts"] == {"feature": "pass"}
    assert second["summary"] == "wrote feature.txt"


def test_a_failing_gate_is_named_in_the_attempt_event(project):
    """Three attempts against a gate that never passes: each says which gate
    refused it, and the budget escalation says why the Run stopped."""
    wrong = _wrong_file()
    engine = make_engine(project, worker=FakeDriver([wrong, wrong, wrong]))
    assert engine.implement("demo/01-write-feature").outcome == "needs-human"

    attempts = events(project, "attempt")
    assert [a["outcome"] for a in attempts] == ["gates-failed"] * 3
    assert attempts[0]["verdicts"] == {"feature": "fail"}
    assert attempts[0]["findings"][0]["gate"] == "feature"
    escalation = events(project, "escalation")[0]
    assert escalation["target"] == "demo/01-write-feature"
    assert "budget exhausted after 3 attempts" in escalation["reason"]
    assert latest(project).outcome == "needs-human"


def _wrong_file():
    """A worker that keeps producing real work the [verify] gate keeps refusing."""
    counter = itertools.count(1)

    def worker(prompt, cwd):
        (cwd / "wrong.txt").write_text(f"not what the gate wants ({next(counter)})\n")
        return {"outcome": "completed", "summary": "wrote the wrong file"}

    return worker


def test_a_worker_escalation_is_recorded_with_its_reason(project):
    escalating = FakeDriver([{"outcome": "needs-human", "summary": "which format?"}])
    engine = make_engine(project, worker=escalating)
    assert engine.implement("demo/01-write-feature").outcome == "needs-human"

    assert events(project, "attempt")[0]["outcome"] == "worker-escalated"
    assert events(project, "escalation")[0]["reason"] == "which format?"
    assert events(project, "exit")[0]["outcome"] == "needs-human"


def test_a_parallel_wave_records_its_merges(project):
    (project / "giro.toml").write_text(PARALLEL_TOML)
    store = Store(project)
    store.create_issue("demo", "Second slice", "Create second.txt.")
    git(project, "add", "-A")
    git(project, "commit", "-m", "two parallel slices")

    def worker(prompt, cwd):
        (cwd / "feature.txt").write_text("ok\n")
        name = "second.txt" if "Second slice" in prompt else "first.txt"
        (cwd / name).write_text("ok\n")
        return {"outcome": "completed", "summary": f"wrote {name}"}

    engine = make_engine(
        project,
        worker=FakeDriver([worker, worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    assert engine.implement("demo").outcome == "all-done"

    assert set(types(project)) <= set(EVENT_TYPES)
    merges = events(project, "merge")
    assert {m["issue"] for m in merges} == {"demo/01-write-feature", "demo/02-second-slice"}
    assert {m["result"] for m in merges} == {"done"}
    assert all(m["branch"].startswith("giro-wt/demo/") for m in merges)
    assert events(project, "wave")[0]["concurrency"] == 2
    assert len(events(project, "claim")) == 2  # every claim in the wave, not just the first


def test_status_overlays_the_live_run_on_its_spec(project):
    """Asked while the machine is mid-attempt, status says so — and where the
    Run has got to."""
    seen = []

    def worker(prompt, cwd):
        seen.append(cli.status_report(project))
        return good_worker(prompt, cwd)

    engine = make_engine(
        project, worker=FakeDriver([worker]), judge=FakeDriver([{"verdict": "pass"}])
    )
    assert engine.implement("demo").outcome == "all-done"

    run = seen[0]["specs"][0]["run"]
    assert run["id"] == latest(project).id and run["target"] == "demo"
    assert run["wave"] == 1 and run["issue"] == "demo/01-write-feature" and run["attempt"] == 1
    assert run["phase"] == "wave 1, 01-write-feature attempt 1"
    # ...and once the Run is over, the overlay is gone: nothing lives on it
    assert cli.status_report(project)["specs"][0]["run"] is None


def test_status_text_names_a_live_run_and_its_phase(project, capsys):
    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    ledger.mark(spec="demo", branch="giro/demo", wave=2, issue="demo/03-third", attempt=1)

    assert cli.main(["-C", str(project), "status"]) == 0
    out = capsys.readouterr().out
    assert f"live run {ledger.id} — wave 2, 03-third attempt 1" in out
    assert f"giro logs {ledger.id}" in out


def test_runs_lists_live_and_recent_runs(project, capsys):
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"
    done = latest(project).id
    live = Ledger.create(Workspace(project).runtime_dir(), "other")
    live.mark(spec="other", stage="activation")

    assert cli.main(["-C", str(project), "runs"]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed[0].split() == [live.id, "live", "other", "activation"]  # live Runs first
    assert listed[1].split()[:3] == [done, "finished", "demo/01-write-feature"]
    assert listed[1].endswith("done — issue done")
    assert len(listed) == 2

    assert cli.main(["-C", str(project), "runs", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["liveness"] for r in rows] == ["live", "finished"]
    assert rows[1]["outcome"] == "done" and rows[1]["spec"] == "demo"
    assert rows[1]["branch"] == "giro/demo" and rows[1]["detached"] is False


def test_runs_says_so_when_there_are_none(project, capsys):
    assert cli.main(["-C", str(project), "runs"]) == 0
    assert "No runs recorded" in capsys.readouterr().out


def test_logs_prints_a_finished_runs_tail(project, capsys):
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    assert engine.implement("demo/01-write-feature").outcome == "done"

    assert cli.main(["-C", str(project), "logs"]) == 0  # no id: the newest Run
    out = capsys.readouterr().out
    assert f"run {latest(project).id} — demo/01-write-feature [finished]" in out
    assert "claim       demo/01-write-feature" in out
    assert "attempt     demo/01-write-feature attempt 1 — green [feature=pass]" in out
    assert "exit        done — issue done" in out


def test_logs_follows_a_live_run_until_it_ends(project, capsys, monkeypatch):
    monkeypatch.setattr(cli, "FOLLOW_POLL", 0.02)
    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    ledger.event("activation", spec="demo", branch="giro/demo", base_branch="main")

    def keep_working():
        for issue in ("01-first", "02-second"):
            ledger.event("claim", issue=f"demo/{issue}")
            ledger.event("attempt", issue=f"demo/{issue}", n=1, outcome="green")
        ledger.finish("all-done", "validate passed")

    worker = threading.Thread(target=keep_working)
    worker.start()
    assert cli.main(["-C", str(project), "logs", ledger.id, "-n", "0", "-f"]) == 0
    worker.join(timeout=5)

    out = capsys.readouterr().out
    assert "claim       demo/01-first" in out  # arrived after the follow began
    assert "attempt     demo/02-second attempt 1 — green" in out
    assert "exit        all-done — validate passed" in out  # ...and it stopped at the end


def test_logs_prints_the_ending_even_when_it_lands_last(project, capsys, monkeypatch):
    """A Run writes its last event and only then says it is over. A follower
    whose final read falls in between must still print the ending — once."""
    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    ledger.event("claim", issue="demo/01-first")
    checks = itertools.count(1)
    real_load = cli.load_run

    def load_run(runtime, run_id):
        if next(checks) == 2:  # the Run ends between the last read and this check
            ledger.finish("all-done", "validate passed")
        return real_load(runtime, run_id)

    monkeypatch.setattr(cli, "load_run", load_run)
    assert cli.main(["-C", str(project), "logs", ledger.id, "-n", "0", "-f"]) == 0

    out = capsys.readouterr().out
    assert out.count("exit        all-done — validate passed") == 1


def test_a_run_writes_the_ledger_entry_it_was_given(project, monkeypatch):
    """`--run-id` is how a dispatched Run claims the entry the dispatch minted
    for it: one Run, one record, written by the process doing the work."""
    engine = make_engine(project, worker=FakeDriver([good_worker]))
    monkeypatch.setattr(cli, "_build_engine", lambda root: engine)
    minted = Ledger.create(Workspace(project).runtime_dir(), "demo/01-write-feature")
    assert cli.main(["-C", str(project), "implement", "demo/01-write-feature",
                     "--run-id", minted.id]) == 0

    assert [r.id for r in list_runs(runtime(project))] == [minted.id]  # no second record
    record = minted.record()
    assert record.pid == os.getpid() and record.outcome == "done"
    assert [e["type"] for e in minted.events()] == ["claim", "attempt", "exit"]


def test_a_run_records_why_it_died_before_it_resolved_its_target(project, capsys, monkeypatch):
    """The engine owns the record from the first line, so a Run refused at the
    door lands in the entry a dispatch is already watching."""
    engine = make_engine(project, worker=FakeDriver([]))
    monkeypatch.setattr(cli, "_build_engine", lambda root: engine)
    minted = Ledger.create(Workspace(project).runtime_dir(), "nonesuch")
    assert cli.main(["-C", str(project), "implement", "nonesuch", "--run-id", minted.id]) == 1

    assert "no spec or issue matches" in capsys.readouterr().err
    assert minted.record().outcome == "error"
    assert [r.id for r in list_runs(runtime(project))] == [minted.id]


def test_two_runs_minted_at_once_never_share_a_ledger(project):
    """Same target, same second, same process: still two Runs. A shared record
    would append one Run's story into the other's and erase its outcome."""
    directory = Workspace(project).runtime_dir()
    first = Ledger.create(directory, "demo")
    second = Ledger.create(directory, "demo")
    assert first.id != second.id

    first.event("claim", issue="demo/01-write-feature")
    second.finish("error", "refused: demo already has a live Run")
    first.finish("done", "issue done")

    assert [e["type"] for e in first.events()] == ["claim", "exit"]
    assert [e["seq"] for e in second.events()] == [1]  # its own stream, from one
    assert first.record().outcome == "done" and second.record().outcome == "error"
    assert {r.id for r in list_runs(runtime(project))} == {first.id, second.id}


def test_a_run_that_dies_before_it_starts_records_why(project, capsys, monkeypatch):
    """However a Run ends — including before it has begun — its Ledger holds the
    reason, so a dispatched Run is never silent about dying."""
    engine = make_engine(project, worker=FakeDriver([]))
    monkeypatch.setattr(cli, "_build_engine", lambda root: engine)
    assert cli.main(["-C", str(project), "implement", "nonesuch"]) == 1
    assert "no spec or issue matches" in capsys.readouterr().err

    run = latest(project)
    assert run.liveness == "finished" and run.outcome == "error"
    assert "no spec or issue matches 'nonesuch'" in run.detail
    assert events(project, "exit")[0]["outcome"] == "error"


def test_logs_refuses_an_unknown_run_id(project, capsys):
    assert cli.main(["-C", str(project), "logs", "nope"]) == 1
    assert "no run 'nope'" in capsys.readouterr().err


def test_deleting_a_finished_runs_ledger_loses_nothing_durable(project, capsys):
    engine = make_engine(
        project, worker=FakeDriver([good_worker]), judge=FakeDriver([{"verdict": "pass"}])
    )
    assert engine.implement("demo").outcome == "all-done"
    shutil.rmtree(runtime(project) / "runs")

    assert cli.main(["-C", str(project), "status"]) == 0
    out = capsys.readouterr().out
    assert "demo [done]" in out and "01-write-feature [done] attempts=1" in out
    assert "live run" not in out
    assert cli.main(["-C", str(project), "runs"]) == 0
    assert "No runs recorded" in capsys.readouterr().out

    # the store is still the memory: a re-invoke resumes from markdown alone
    worker = FakeDriver([good_worker])
    again = make_engine(project, worker=worker)
    assert again.implement("demo").detail.startswith("already done")
    assert worker.calls == []
    assert spec_store(project).load_issues("demo")[0].state == "done"
