"""Phase lines on stderr while ``giro implement`` runs.

A human watching the loop reads what is happening as it happens: one short
line per state-changing moment, on stderr, sibling to the on-disk event
stream. ``--quiet`` suppresses that surface without touching the final report
or the Ledger. A dispatched Run's ``console.log`` captures the same lines,
because the subprocess' stderr already lands there.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from giro.drivers import FakeDriver
from giro.ledger import Ledger
from giro.store import Store
from giro.workspace import Workspace
from tests.conftest import MINIMAL_TOML, git, good_worker, make_engine

PARALLEL_TOML = MINIMAL_TOML.replace("concurrency = 1", "concurrency = 2")


def _capture_phase(engine, target: str, quiet: bool = False):
    """Run one engine.implement() and return the phase-line text plus the report.

    Uses a dedicated ``phase_stream`` so a parallel wave's threads land in one
    buffer regardless of how the test harness captures ``sys.stderr``.
    """
    buffer = io.StringIO()
    real_open = Ledger.open

    def open_with_buffer(runtime, target, run_id="", *, quiet=False):  # noqa: ARG001
        ledger = real_open(runtime, target, run_id=run_id, quiet=quiet)
        ledger._phase_stream = buffer
        return ledger

    Ledger.open = staticmethod(open_with_buffer)  # type: ignore[assignment]
    try:
        report = engine.implement(target, quiet=quiet)
    finally:
        Ledger.open = real_open  # type: ignore[assignment]
    return buffer.getvalue(), report


# -- foreground: every trigger prints one line -------------------------------


def test_phase_lines_narrate_the_issue_loop_on_stderr(project):
    """A foreground Issue Run prints a claim, a worker start, a verify line
    naming the gate and its verdict, a worker end, and a state transition —
    one moment each, on stderr, every line short and never JSON."""
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    err, report = _capture_phase(engine, "demo/01-write-feature")

    assert report.outcome == "done"
    lines = [line for line in err.splitlines() if line.startswith(">> ")]
    assert lines, err

    joined = "\n".join(lines)
    assert ">> claim demo/01-write-feature" in joined
    assert ">> worker demo/01-write-feature attempt 1" in joined
    assert ">> worker demo/01-write-feature attempt 1 — green" in joined
    assert ">> verify demo/01-write-feature: feature=pass" in joined  # gate=verdict named
    assert ">> state demo/01-write-feature → done" in joined

    # short, human-readable, never JSON
    for line in lines:
        assert len(line) < 200, line
        assert "{" not in line and "}" not in line, line


def test_phase_lines_narrate_the_spec_loop(project):
    """A Spec Run narrates its activation, the wave that runs, and the
    validate verdict that closes it — spec-level state transitions included."""
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    err, report = _capture_phase(engine, "demo")

    assert report.outcome == "all-done"
    assert ">> state demo → active" in err
    assert ">> wave 1 — 01-write-feature" in err
    assert ">> claim demo/01-write-feature" in err
    assert ">> verify demo/01-write-feature: feature=pass" in err
    assert ">> validate demo: spec-fit=pass" in err
    assert ">> state demo → done" in err


def test_parallel_wave_narrates_its_merges(project):
    """concurrency>1 waves narrate their merges to stderr, one line per merge
    (and both claims, not just the first)."""
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
    err, report = _capture_phase(engine, "demo")

    assert report.outcome == "all-done"
    assert ">> merge demo/01-write-feature — done" in err
    assert ">> merge demo/02-second-slice — done" in err
    assert ">> claim demo/01-write-feature" in err
    assert ">> claim demo/02-second-slice" in err


# -- --quiet: no stderr surface, stdout and Ledger untouched ------------------


def test_quiet_suppresses_phase_lines_entirely(project):
    """A ``quiet=True`` Run writes not one ``>>`` line to its phase stream."""
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    )
    err, report = _capture_phase(engine, "demo/01-write-feature", quiet=True)

    assert report.outcome == "done"
    assert err == ""  # nothing said on stderr
    # ...and the Ledger's own events are intact — quiet is human-surface only
    from giro.ledger import list_runs
    events_file = list_runs(Workspace(project).runtime_path())[0].dir / "events.jsonl"
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert [e["type"] for e in events] == ["claim", "attempt", "exit"]


def test_quiet_stdout_is_byte_identical_to_a_noisy_run(project, tmp_path):
    """The final stdout report ``giro implement`` prints is byte-identical
    with and without ``--quiet``. Two real subprocesses so stdout is measured
    as a human sees it, and quiet's effect is scoped to the phase surface.
    """
    # A stub `claude` on PATH so no tokens are spent and both runs behave the
    # same way as the tests above (using a FakeDriver would skip the CLI).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        "printf 'ok\\n' > feature.txt\n"
        "echo '{\"outcome\": \"completed\", \"summary\": \"wrote feature.txt\"}'\n"
    )
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    def run(target: str, extra: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "giro", "-C", str(project), "implement", target, *extra],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    noisy = run("demo/01-write-feature", [])
    assert noisy.returncode == 0, noisy.stderr
    # Reset the world: drop the giro branch, its worktree, and the Run history
    # so a second attempt does the same work from the same starting point.
    git(project, "checkout", "main")
    git(project, "worktree", "remove", "--force", str(project / ".giro" / "worktrees" / "demo"))
    git(project, "branch", "-D", "giro/demo")
    import shutil
    shutil.rmtree(project / ".giro", ignore_errors=True)

    quiet = run("demo/01-write-feature", ["--quiet"])
    assert quiet.returncode == 0, quiet.stderr

    assert quiet.stdout == noisy.stdout  # byte-identical final report
    # noisy stderr carried phase lines; quiet stderr is free of them
    assert ">> " in noisy.stderr
    assert ">> " not in quiet.stderr


# -- detached: phase lines land in console.log --------------------------------

DISPATCH_TIMEOUT = 20
RUN_TIMEOUT = 120

STUB_CLAUDE = """\
#!/bin/sh
printf 'ok\\n' > feature.txt
echo '{"outcome": "completed", "summary": "wrote feature.txt"}'
"""


@pytest.fixture
def stub_agent(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(STUB_CLAUDE)
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _dispatch(project: Path, target: str, env: dict[str, str], extra: list[str]) -> str:
    command = (
        f"{sys.executable} -m giro -C {project} implement {target} --detach "
        f"{' '.join(extra)}"
    )
    proc = subprocess.run(
        ["sh", "-c", command],
        capture_output=True,
        text=True,
        env=env,
        timeout=DISPATCH_TIMEOUT,
    )
    assert proc.returncode == 0, proc.stderr
    first = proc.stdout.splitlines()[0]
    assert first.startswith("dispatched "), proc.stdout
    return first.split()[1]


def _wait_for_finish(project: Path, run_id: str) -> dict:
    directory = project / ".giro" / "runs" / run_id
    deadline = time.monotonic() + RUN_TIMEOUT
    while time.monotonic() < deadline:
        record = json.loads((directory / "run.json").read_text())
        if record["state"] == "finished":
            return record
        time.sleep(0.2)
    console = (directory / "console.log").read_text(errors="replace")
    raise AssertionError(f"Run {run_id} never finished; console said:\n{console}")


def test_a_dispatched_runs_console_log_carries_the_phase_lines(project, stub_agent):
    """A dispatched Run's stderr is captured into ``console.log`` — so the
    same lines a foreground Run prints land there for later reading."""
    run_id = _dispatch(project, "demo/01-write-feature", stub_agent, extra=[])
    record = _wait_for_finish(project, run_id)
    assert record["outcome"] == "done"

    console = (project / ".giro" / "runs" / run_id / "console.log").read_text(
        errors="replace"
    )
    assert ">> claim demo/01-write-feature" in console
    assert ">> worker demo/01-write-feature attempt 1" in console
    assert ">> verify demo/01-write-feature: feature=pass" in console
    assert ">> state demo/01-write-feature → done" in console


def test_a_dispatched_quiet_runs_console_log_carries_no_phase_lines(project, stub_agent):
    """``--detach --quiet`` is accepted, and the dispatched Run's
    ``console.log`` carries no phase lines — the on-disk event stream is
    unchanged, because quiet is a human-surface flag only."""
    run_id = _dispatch(project, "demo/01-write-feature", stub_agent, extra=["--quiet"])
    record = _wait_for_finish(project, run_id)
    assert record["outcome"] == "done"

    console = (project / ".giro" / "runs" / run_id / "console.log").read_text(
        errors="replace"
    )
    assert ">> " not in console

    events_file = project / ".giro" / "runs" / run_id / "events.jsonl"
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert [e["type"] for e in events] == ["claim", "attempt", "exit"]
