"""Dispatch at the process level: the Run outlives the shell that started it.

Everything here is real — a real shell, a real detached process, a real agent
CLI on PATH (a stub one, so no tokens are spent). What is being proved is the
part in-process tests cannot reach: `--detach` returns at once, the Run keeps
going after its invoking shell is gone, and its outcome lands in the Ledger for
whoever asks later.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from giro import cli
from giro.ledger import list_runs, read_events
from giro.runs import process_alive
from tests.conftest import spec_store

DISPATCH_TIMEOUT = 20  # seconds `giro implement --detach` may take to return
RUN_TIMEOUT = 120  # seconds the detached Run may take to finish

STUB_CLAUDE = """\
#!/bin/sh
# Stands in for the `claude` CLI: does the work in cwd, ends with an envelope.
printf 'ok\\n' > feature.txt
echo '{"outcome": "completed", "summary": "wrote feature.txt"}'
"""

BLOCKED_CLAUDE = """\
#!/bin/sh
# The same stub, except it will not finish until the test lets it: a Run that
# cannot end on its own is what makes "did dispatch return first?" answerable.
while [ ! -f "$GIRO_TEST_RELEASE" ]; do sleep 0.05; done
printf 'ok\\n' > feature.txt
echo '{"outcome": "completed", "summary": "wrote feature.txt"}'
"""


def agent_on_path(bin_dir: Path, script: str) -> dict[str, str]:
    """An environment whose `claude` is this shell script — the roster's default
    driver, without the model."""
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(script)
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


@pytest.fixture
def stub_agent(tmp_path: Path) -> dict[str, str]:
    return agent_on_path(tmp_path / "bin", STUB_CLAUDE)


@pytest.fixture
def blocked_agent(tmp_path: Path):
    """An agent that hangs until the returned path exists, and is always let go
    at the end so no stub outlives its test."""
    release = tmp_path / "release"
    env = agent_on_path(tmp_path / "blocked-bin", BLOCKED_CLAUDE)
    yield {**env, "GIRO_TEST_RELEASE": str(release)}, release
    release.touch()


def try_dispatch(project: Path, target: str, env: dict[str, str]):
    """Dispatch from a shell that exits immediately — the invoking shell is
    gone by the time the Run is anywhere near done."""
    command = f"{sys.executable} -m giro -C {project} implement {target} --detach"
    return subprocess.run(
        ["sh", "-c", command],
        capture_output=True,
        text=True,
        env=env,
        timeout=DISPATCH_TIMEOUT,
    )


def dispatch(project: Path, target: str, env: dict[str, str]) -> str:
    """Dispatch and return the Run id it printed."""
    proc = try_dispatch(project, target, env)
    assert proc.returncode == 0, proc.stderr
    first = proc.stdout.splitlines()[0]
    assert first.startswith("dispatched "), proc.stdout
    return first.split()[1]


def run_dir(project: Path, run_id: str) -> Path:
    return project / ".giro" / "runs" / run_id


def read_record(project: Path, run_id: str) -> dict:
    return json.loads((run_dir(project, run_id) / "run.json").read_text())


def console(project: Path, run_id: str) -> str:
    return (run_dir(project, run_id) / "console.log").read_text(errors="replace")


def wait_for_outcome(project: Path, run_id: str) -> dict:
    deadline = time.monotonic() + RUN_TIMEOUT
    while time.monotonic() < deadline:
        record = read_record(project, run_id)
        if record["state"] == "finished":
            return record
        time.sleep(0.2)
    raise AssertionError(
        f"the detached Run never finished; its console said:\n{console(project, run_id)}"
    )


def wait_for_event(project: Path, run_id: str, kind: str) -> dict:
    """Wait for one moment to reach a still-working Run's stream.

    A Run that ended instead never got there — say so with its console, rather
    than waiting out the timeout on a process that is already gone.
    """
    deadline = time.monotonic() + RUN_TIMEOUT
    while time.monotonic() < deadline:
        for event in read_events(run_dir(project, run_id)):
            if event["type"] == kind:
                return event
        if read_record(project, run_id)["state"] == "finished":
            raise AssertionError(
                f"the Run ended before any {kind!r}; its console said:"
                f"\n{console(project, run_id)}"
            )
        time.sleep(0.05)
    raise AssertionError(
        f"no {kind!r} event within {RUN_TIMEOUT}s; the console said:"
        f"\n{console(project, run_id)}"
    )


def test_a_dispatched_run_outlives_the_shell_and_lands_in_the_ledger(project, stub_agent, capsys):
    """The shell that dispatched it is long gone by the time this Run finishes,
    and its outcome is still there to read."""
    run_id = dispatch(project, "demo/01-write-feature", stub_agent)
    record = wait_for_outcome(project, run_id)

    assert record["detached"] is True and record["outcome"] == "done"
    assert record["target"] == "demo/01-write-feature" and record["spec"] == "demo"
    assert record["pid"] != os.getpid()  # a process of its own, not this one
    kinds = [e["type"] for e in read_events(project / ".giro" / "runs" / run_id)]
    assert kinds == ["claim", "attempt", "exit"]  # an Issue Run: no wave, no plan

    # the work itself landed on the Spec branch, exactly as a foreground Run's would
    assert spec_store(project).load_issues("demo")[0].state == "done"
    assert (project / ".giro" / "worktrees" / "demo" / "feature.txt").is_file()

    # ...and the outcome is readable after the fact, with no live process left
    assert cli.main(["-C", str(project), "runs"]) == 0
    listed = capsys.readouterr().out
    assert run_id in listed and "finished" in listed and "detached" in listed
    assert cli.main(["-C", str(project), "logs", run_id]) == 0
    assert "exit        done — issue done" in capsys.readouterr().out


def test_dispatch_returns_while_its_run_is_still_working(project, blocked_agent):
    """Dispatch returns *before* the Run it started can possibly end.

    This Run's worker hangs until the test releases it, and the test cannot
    release it until the dispatching process has already handed back the Run
    id — so a `--detach` that ran the loop in the foreground could never get
    here at all: it would still be inside the loop when the timeout fired.
    """
    env, release = blocked_agent

    run_id = dispatch(project, "demo/01-write-feature", env)  # blocking → TimeoutExpired

    # the terminal came back while the Run was still working, in its own process
    record = read_record(project, run_id)
    assert record["state"] in ("starting", "running") and record["outcome"] == ""
    assert record["pid"] != os.getpid() and process_alive(record["pid"])

    # ...and it goes on advancing with nobody holding it: it reaches the worker
    # on its own, and there it waits for a release only this test can give
    claim = wait_for_event(project, run_id, "claim")
    assert claim["issue"] == "demo/01-write-feature"
    assert read_record(project, run_id)["state"] != "finished"

    release.touch()  # only now can the Run end — long after its caller returned

    finished = wait_for_outcome(project, run_id)
    assert finished["outcome"] == "done" and finished["detached"] is True
    kinds = [e["type"] for e in read_events(run_dir(project, run_id))]
    assert kinds == ["claim", "attempt", "exit"]
    assert spec_store(project).load_issues("demo")[0].state == "done"


def test_dispatch_returns_before_the_run_does(project, stub_agent):
    """The terminal comes back at once: the Run is still live when it does."""
    started = time.monotonic()
    run_id = dispatch(project, "demo", stub_agent)
    assert time.monotonic() - started < DISPATCH_TIMEOUT

    record = json.loads((project / ".giro" / "runs" / run_id / "run.json").read_text())
    assert record["state"] in ("starting", "running", "finished")
    assert record["id"] == run_id
    wait_for_outcome(project, run_id)  # ...and it still finishes on its own


def test_dispatching_an_unknown_target_fails_at_the_prompt(project, stub_agent):
    """What can be said loudly is said before the terminal is handed back —
    never left in a Ledger nobody has been told to read."""
    proc = try_dispatch(project, "nonesuch", stub_agent)

    assert proc.returncode == 1
    assert "no spec or issue matches 'nonesuch'" in proc.stderr
    assert list_runs(project / ".giro") == []  # nothing was started, nothing recorded


def test_dispatching_a_spec_with_a_live_run_is_refused_loudly(project, stub_agent):
    """Double-dispatch fails at once and by name, detached or not — it is never
    queued, and no second process is ever started."""
    lock = project / ".giro" / "locks" / "demo.json"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), "target": "demo", "started": "2026-01-01T00:00:00+00:00"})
    )

    proc = try_dispatch(project, "demo", stub_agent)

    assert proc.returncode == 1
    assert "already has a live Run" in proc.stderr and str(os.getpid()) in proc.stderr
    assert "refused rather than queued" in proc.stderr
    assert list_runs(project / ".giro") == []
