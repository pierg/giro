"""The Ledger — the disposable per-Run record.

Three things about one Run: whether it is still alive, what it has done (an
append-only event stream, one line per state-changing moment), and how it
ended. Nothing else reads it for control flow: markdown is the memory
(ADR-0008), so deleting a finished Run's Ledger loses observability and
nothing else — resume, status, and re-invoke all still work from the store.

The event stream is the seam the GitHub projection will consume: one JSON
object per line, ``{seq, at, type, ...}``, appended and never rewritten.

A ``Ledger()`` with no directory is the Run nobody is watching: every write is
a no-op, so the loops can narrate unconditionally.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .runs import RunError, now_iso, process_alive

RUNS_DIR = "runs"
RUN_FILE = "run.json"
EVENTS_FILE = "events.jsonl"
CONSOLE_FILE = "console.log"

# The public contract's semver-shaped signal. Every event carries
# ``schema_version`` as a top-level field; a consumer that meets an event
# without one treats it as pre-1. Bump when the contract changes.
SCHEMA_VERSION = 1

# One line per state-changing moment. The set is closed on purpose: a consumer
# of the stream (today status and `giro logs`; tomorrow the GitHub projection)
# can switch on it exhaustively.
EVENT_TYPES = (
    "activation",
    "plan",
    "wave",
    "claim",
    "attempt",
    "merge",
    "gap-cycle",
    "escalation",
    "projection",
    "exit",
)


def runs_dir(runtime: Path) -> Path:
    return runtime / RUNS_DIR


def new_run_id(target: str) -> str:
    """A Run identifier a human can read back to the engine: what it works on,
    when it started, and which process.

    Readable, not unique — two Runs of one target in one second in one process
    mint the same name. ``_fresh_dir`` is what settles that.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-") or "run"
    return f"{name}-{stamp}-{os.getpid()}"


def _fresh_dir(parent: Path, run_id: str) -> Path:
    """A directory no other Run owns, for a Run that wants ``run_id``.

    ``mkdir`` without ``exist_ok`` is the exclusive create that decides it: a
    Run that reused another's directory would append its events into that Run's
    stream and overwrite its record — one Run's outcome lost, the other's story
    told twice. Whoever arrives second is ``-2``, ``-3``, and so on.
    """
    parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        path = parent / (run_id if n == 1 else f"{run_id}-{n}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            n += 1


@dataclass
class RunRecord:
    """One Run as its Ledger tells it — liveness, phase, and outcome."""

    id: str
    dir: Path
    target: str = ""
    spec: str = ""
    branch: str = ""
    pid: int = 0
    detached: bool = False
    started: str = ""
    finished: str = ""
    state: str = "starting"  # starting | running | finished
    outcome: str = ""  # done | all-done | needs-human | error, once finished
    detail: str = ""
    stage: str = ""  # the last event type — where the Run is
    wave: int = 0
    issue: str = ""
    attempt: int = 0

    @property
    def liveness(self) -> str:
        """live, dead, or finished — a Run that died without a word is not
        'running', however its record was left."""
        if self.state == "finished":
            return "finished"
        if self.pid and not process_alive(self.pid):
            return "dead"
        return "live"

    @property
    def live(self) -> bool:
        return self.liveness == "live"

    @property
    def phase(self) -> str:
        """What the Run is doing now, or what it ended as."""
        if self.state == "finished":
            return f"{self.outcome} — {self.detail}" if self.detail else self.outcome
        if self.liveness == "dead":
            return f"died during {self.stage or 'startup'}"
        parts = []
        if self.wave:
            parts.append(f"wave {self.wave}")
        if self.issue:
            attempt = f" attempt {self.attempt}" if self.attempt else ""
            parts.append(f"{self.issue.rpartition('/')[2]}{attempt}")
        return ", ".join(parts) or self.stage or "starting"


def _record(directory: Path, data: dict[str, Any]) -> RunRecord:
    return RunRecord(
        id=str(data.get("id", directory.name)),
        dir=directory,
        target=str(data.get("target", "")),
        spec=str(data.get("spec", "")),
        branch=str(data.get("branch", "")),
        pid=int(data.get("pid", 0) or 0),
        detached=bool(data.get("detached", False)),
        started=str(data.get("started", "")),
        finished=str(data.get("finished", "")),
        state=str(data.get("state", "starting")),
        outcome=str(data.get("outcome", "")),
        detail=str(data.get("detail", "")),
        stage=str(data.get("stage", "")),
        wave=int(data.get("wave", 0) or 0),
        issue=str(data.get("issue", "")),
        attempt=int(data.get("attempt", 0) or 0),
    )


def read_run(directory: Path) -> RunRecord | None:
    """The Run a directory records, or None when there is nothing readable —
    an unreadable Ledger is missing observability, never an error."""
    try:
        data = json.loads((directory / RUN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _record(directory, data) if isinstance(data, dict) else None


def read_events(directory: Path) -> list[dict[str, Any]]:
    try:
        lines = (directory / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a half-written last line: the story, not the memory
        if isinstance(event, dict):
            events.append(event)
    return events


def list_runs(runtime: Path) -> list[RunRecord]:
    """Every Run this project has a Ledger for, newest first."""
    directory = runs_dir(runtime)
    if not directory.is_dir():
        return []
    runs = [run for child in directory.iterdir() if (run := read_run(child)) is not None]
    return sorted(runs, key=lambda r: (r.started, r.id), reverse=True)


def load_run(runtime: Path, run_id: str) -> RunRecord:
    """One Run by identifier, or the newest when none is named."""
    if run_id:
        run = read_run(runs_dir(runtime) / run_id)
        if run is None:
            raise RunError(f"no run {run_id!r} in {runs_dir(runtime)} — `giro runs` lists them")
        return run
    runs = list_runs(runtime)
    if not runs:
        raise RunError("no runs recorded yet — dispatch one with `giro implement <target>`")
    return runs[0]


def live_runs_by_spec(runtime: Path) -> dict[str, RunRecord]:
    """The live Run per Spec, for status to overlay. Newest wins if a dead
    record and a live one both name the same Spec."""
    live: dict[str, RunRecord] = {}
    for run in list_runs(runtime):
        if run.live and run.spec and run.spec not in live:
            live[run.spec] = run
    return live


class Ledger:
    """One Run's record, written as the Run happens.

    ``Ledger()`` — with no directory — is the Run nobody is watching: every
    write is a no-op, so an embedded caller pays nothing and the loops never
    branch on whether anyone is looking.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        quiet: bool = False,
        phase_stream: TextIO | None = None,
    ):
        self.dir = directory
        self.quiet = quiet
        # Resolve stderr at call time by default, so pytest's capsys — which
        # swaps sys.stderr per test — sees the phase lines.
        self._phase_stream = phase_stream
        self._lock = threading.Lock()
        self._seq = len(read_events(directory)) if directory is not None else 0

    # -- opening --------------------------------------------------------------

    @classmethod
    def create(
        cls, runtime: Path, target: str, detached: bool = False, *, quiet: bool = False
    ) -> Ledger:
        """Mint a new Run's Ledger. A dispatched Run's record is created by the
        shell that dispatches it, before the Run's own process exists — so it
        starts with no pid, and the Run itself claims it."""
        directory = _fresh_dir(runs_dir(runtime), new_run_id(target))
        run_id = directory.name
        ledger = cls(directory, quiet=quiet)
        ledger._write(
            {
                "id": run_id,
                "target": target,
                "spec": "",
                "branch": "",
                "pid": 0 if detached else os.getpid(),
                "detached": detached,
                "started": now_iso(),
                "state": "starting" if detached else "running",
                "outcome": "",
                "detail": "",
                "finished": "",
                "stage": "",
                "wave": 0,
                "issue": "",
                "attempt": 0,
            }
        )
        return ledger

    @classmethod
    def open(
        cls, runtime: Path, target: str, run_id: str = "", *, quiet: bool = False
    ) -> Ledger:
        """The Ledger this process writes: the one it was dispatched with, or a
        fresh one for a Run started in the foreground."""
        if not run_id:
            return cls.create(runtime, target, quiet=quiet)
        directory = runs_dir(runtime) / run_id
        if not (directory / RUN_FILE).is_file():
            raise RunError(f"no run {run_id!r} to attach to in {runs_dir(runtime)}")
        ledger = cls(directory, quiet=quiet)
        ledger.mark(pid=os.getpid(), state="running")
        return ledger

    @property
    def id(self) -> str:
        return self.dir.name if self.dir is not None else ""

    @property
    def console(self) -> Path:
        """Where a dispatched Run's own output goes — a crash that never reached
        an event still leaves its traceback somewhere a human can read."""
        if self.dir is None:
            raise RunError("a Run nobody is watching has no console to write to")
        return self.dir / CONSOLE_FILE

    # -- writing --------------------------------------------------------------

    def event(self, kind: str, **fields: Any) -> None:
        """Append one state-changing moment. Safe from a wave's threads: the
        engine is one process, and the stream is append-only."""
        if self.dir is None:
            return
        with self._lock:
            self._seq += 1
            line = json.dumps({
                "schema_version": SCHEMA_VERSION,
                "seq": self._seq,
                "at": now_iso(),
                "type": kind,
                **fields,
            })
            with open(self.dir / EVENTS_FILE, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._update({"stage": kind})

    def phase(self, text: str) -> None:
        """One human-readable line about where the Run is now, for someone
        watching stderr while ``giro implement`` runs.

        A sibling to :meth:`event`, not a consumer of it: the two are called at
        the same triggers, so silence in one never implies silence in the
        other. Independent of the on-disk record — a ``Ledger()`` with no
        directory still narrates to stderr — because the human surface is not
        the machine surface. Suppressed by ``quiet``.

        The output stream is resolved at call time so a test that swaps
        ``sys.stderr`` (pytest's ``capsys``) sees the line.
        """
        if self.quiet:
            return
        stream = self._phase_stream if self._phase_stream is not None else sys.stderr
        with self._lock:
            try:
                stream.write(f">> {text}\n")
                stream.flush()
            except (OSError, ValueError):
                pass  # a closed stderr is a lost narration, never a Run failure

    def mark(self, **fields: Any) -> None:
        """Update where the Run is, so status can say so without replaying the
        whole stream."""
        if self.dir is None:
            return
        with self._lock:
            self._update(fields)

    def finish(self, outcome: str, detail: str = "") -> None:
        """The Run's last word. Idempotent: the first outcome recorded is the
        one that happened."""
        if self.dir is None:
            return
        current = self.record()
        if current is not None and current.state == "finished":
            return
        self.event("exit", outcome=outcome, detail=detail)
        self.mark(state="finished", outcome=outcome, detail=detail, finished=now_iso())

    # -- reading --------------------------------------------------------------

    def record(self) -> RunRecord | None:
        return read_run(self.dir) if self.dir is not None else None

    def events(self) -> list[dict[str, Any]]:
        return read_events(self.dir) if self.dir is not None else []

    # -- the record file ------------------------------------------------------

    def _update(self, fields: dict[str, Any]) -> None:
        assert self.dir is not None
        try:
            data = json.loads((self.dir / RUN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"id": self.dir.name}
        if not isinstance(data, dict):
            data = {"id": self.dir.name}
        data.update(fields)
        self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        assert self.dir is not None
        tmp = self.dir / f"{RUN_FILE}.tmp"
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.dir / RUN_FILE)  # a reader never sees half a record


# -- rendering ---------------------------------------------------------------


def _listed(values: Any, limit: int = 4) -> str:
    items = [str(v) for v in values] if isinstance(values, list) else []
    if len(items) > limit:
        return ", ".join(items[:limit]) + f", +{len(items) - limit} more"
    return ", ".join(items)


def _summary(event: dict[str, Any]) -> str:
    kind = event.get("type", "")
    if kind == "activation":
        base = event.get("base_branch") or "?"
        return f"{event.get('spec', '')} active on {event.get('branch', '')} (base {base})"
    if kind == "plan":
        issues = event.get("issues", [])
        return f"{len(issues) if isinstance(issues, list) else 0} issue(s): {_listed(issues)}"
    if kind == "wave":
        return f"wave {event.get('wave', '?')}: {_listed(event.get('issues', []))}"
    if kind == "claim":
        return str(event.get("issue", ""))
    if kind == "attempt":
        line = (
            f"{event.get('issue', '')} attempt {event.get('n', '?')}"
            f" — {event.get('outcome', '')}"
        )
        verdicts = event.get("verdicts", {})
        if isinstance(verdicts, dict) and verdicts:
            line += " [" + ", ".join(f"{g}={v}" for g, v in verdicts.items()) + "]"
        return line + _findings(event)
    if kind == "merge":
        return f"{event.get('issue', '')} — {event.get('result', '')}" + _findings(event)
    if kind == "gap-cycle":
        return (
            f"cycle {event.get('cycle', '?')} — {_listed(event.get('issues', []))} filed"
            + _findings(event)
        )
    if kind == "escalation":
        return f"{event.get('target', '')}: {event.get('reason', '')}"
    if kind == "projection":
        state = "ok" if event.get("ok") else "failed"
        return f"{event.get('action', '')} {state} — {event.get('detail', '')}"
    if kind == "exit":
        detail = event.get("detail", "")
        return f"{event.get('outcome', '')}" + (f" — {detail}" if detail else "")
    return json.dumps({k: v for k, v in event.items() if k not in ("seq", "at", "type")})


def _findings(event: dict[str, Any]) -> str:
    findings = event.get("findings", [])
    if not isinstance(findings, list) or not findings:
        return ""
    summaries = [str(f.get("summary", "")) for f in findings if isinstance(f, dict)]
    return "; " + _listed(summaries, limit=3)


def render_event(event: dict[str, Any]) -> str:
    at = str(event.get("at", ""))[11:19] or "--:--:--"
    return f"{at}  {str(event.get('type', '?')):<10}  {_summary(event)}"
