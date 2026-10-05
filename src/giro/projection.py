"""Projection — the store's state, rendered onto GitHub one way (ADR-0014).

Display, never memory and never control: the engine renders outward and never
reads GitHub back into a decision. Gates fail closed; the Projection fails
soft at *runtime* — a rate limit or a transient network glitch costs
visibility, not work, so every call here *answers*, records what happened in
the Ledger, and lets the loop carry on. Preflight is the exception: when
``[github_projection] enabled = true``, the Run refuses to start if ``gh``,
the token, or the repository are not in order — the operator asked for the
surface, so "silently off" would be the wrong answer.

This module holds the substrate — configuration, the token, a timed ``gh``
runner, preflight, the ``giro:`` label set — the Spec's own surface (the parent
tracking issue, its labels, and the draft pull request whose engine zone
re-renders the Issue checklist at every checkpoint), every giro Issue as a
sub-issue of that parent wearing the state the store gives it, the Run's
narration — each appended log section as one comment on its sub-issue, each
orchestration moment as one comment on the parent, and the integrated gate
verdicts as commit statuses on the published head — and the two moments a human
must feel: needs-human as a comment and an assignment that notify, and Validate
green as the draft pull request flipped ready for review, with the merge itself
left where it belongs (ADR-0005).

The desired surface is computed by pure functions of the store, and every write
is compared against what GitHub holds before it is made — so projecting twice
changes nothing, and human prose outside the engine's fence is never touched.
Every generated body carries a hidden marker, so a surface whose identifiers the
store never recorded can be found rather than made twice; ``giro.reconcile``
leans on that to reconverge a repository from the markdown alone.

``Projection()`` — with no runner — is Projection off: every call is a no-op,
so the loops never branch on whether anyone is looking.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from .config import Config, ProjectionSettings
from .envelope import Finding
from .ledger import Ledger
from .states import ISSUE_TERMINAL
from .store import Issue, Spec, log_sections
from .workspace import Workspace

ENV_FILE = ".env"  # the project's git-ignored env file — no worktree ever holds it
TOKEN_VAR = "GITHUB_TOKEN"

OFF = -2  # a call not made, because Projection is off
UNRUN = -1  # a call that never reached GitHub: no binary, a hang, a dead network

# The `giro:` label set — stable names and stable colors, so one projected
# surface reads the same in every repository. Created when missing and never
# renamed; a human recolouring one is drift the next Reconcile leaves alone.
LABELS: tuple[tuple[str, str, str], ...] = (
    ("giro:spec", "5319e7", "giro: a Spec's tracking issue"),
    ("giro:issue", "1d76db", "giro: an Issue of a giro Spec"),
    ("giro:ready", "c2e0c6", "giro: waiting for a worker"),
    ("giro:in-progress", "fbca04", "giro: a worker is on it"),
    ("giro:needs-human", "d93f0b", "giro: the loop is waiting on a human"),
    ("giro:done", "0e8a16", "giro: verified done"),
    ("giro:wontfix", "cfd3d7", "giro: closed as not planned"),
    ("giro:gap", "e99695", "giro: filed by a Validate gate"),
)

GITHUB_REMOTE = re.compile(r"github\.com[:/]+([^/]+?)/(.+?)(?:\.git)?/?$")
ISSUE_NUMBER = re.compile(r"/(?:issues|pull)/(\d+)")

# The hidden marker every generated *body* carries — the mapping written where
# GitHub itself keeps it, so a Reconcile can find a surface the store has no
# identifier for: a clone that never ran the Run, an outage that swallowed the
# identifier commit, a human who moved the issue.
BODY_MARKER = re.compile(r"<!-- (giro:(?:spec|issue):\S+) -->")
DISCOVER_LIMIT = 500  # issues read per giro label when looking for markers

# The hidden marker every generated comment carries, and the key that says
# which moment it is: re-projecting that moment edits this comment instead of
# posting a second one. A comment without one is a human's, and is never
# touched.
COMMENT_MARKER = re.compile(r"<!-- giro:comment:(\S+) -->")

# GitHub's secondary rate limit is a request to slow down, not a refusal: a
# content-creating call waits once and asks again, then drops fail-soft.
SECONDARY_LIMIT = re.compile(r"secondary rate limit|abuse detection", re.IGNORECASE)
RETRY_PAUSE = 2.0  # seconds
WRITE_VERBS = frozenset({"create", "edit", "close", "reopen", "comment", "delete"})
WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
# Every write is a change a human can see; `pr ready` is a change that writes no
# content — nothing for a secondary limit to object to, and still worth saying.
CHANGE_VERBS = WRITE_VERBS | {"ready"}

# A gate verdict, as a commit status: green is literally green.
STATUS_STATE = {"pass": "success"}

# The engine's fence inside a pull-request body. Everything between the markers
# is rendered from the store and overwritten at every checkpoint; everything
# outside them is the human's prose, and the engine never touches it.
ZONE_START = "<!-- giro:engine-zone -->"
ZONE_END = "<!-- /giro:engine-zone -->"

# The labels the parent tracking issue may carry, so a state change removes the
# one it replaces and leaves every human label alone.
SPEC_LABELS = ("giro:spec", "giro:needs-human", "giro:done")

# The same, for a sub-issue: one per Issue state, plus the two that say what
# kind of thing it is. A label outside this set is a human's and is never
# touched.
ISSUE_LABELS = (
    "giro:issue",
    "giro:ready",
    "giro:in-progress",
    "giro:needs-human",
    "giro:done",
    "giro:wontfix",
    "giro:gap",
)

# How a terminal Issue closes: verified work is completed, a human's wontfix is
# not planned. Every other state leaves the sub-issue open.
CLOSE_REASON = {"done": "completed", "wontfix": "not planned"}


class ProjectionError(Exception):
    """Preflight failed while [github_projection] enabled = true — refuse the Run."""


@dataclass
class GhResult:
    """One ``gh`` invocation's answer. Never an exception — that is the point."""

    code: int
    stdout: str = ""
    stderr: str = ""
    args: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def error(self) -> str:
        detail = self.stderr.strip() or self.stdout.strip()
        return detail[-500:] if detail else f"`gh {' '.join(self.args)}` exited {self.code}"

    def json(self) -> Any:
        """The parsed body of a ``--json`` call, or None when there is none."""
        try:
            return json.loads(self.stdout)
        except ValueError:
            return None


class GhRunner(Protocol):
    def run(self, args: list[str]) -> GhResult: ...


class SubprocessGh:
    """``gh``, given a timeout and a token that lives only in the subprocess
    environment — never on the command line, never in a Ledger event."""

    def __init__(self, timeout: int = 30, token: str = "", cwd: Path | None = None):
        self.timeout = timeout
        self.cwd = cwd
        self._env = {**os.environ, TOKEN_VAR: token} if token else None

    def run(self, args: list[str]) -> GhResult:
        try:
            proc = subprocess.run(
                ["gh", *args],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._env,
            )
        except FileNotFoundError:
            return GhResult(UNRUN, stderr="`gh` CLI not found on PATH", args=list(args))
        except subprocess.TimeoutExpired:
            return GhResult(
                UNRUN,
                stderr=f"`gh {' '.join(args)}` timed out after {self.timeout}s",
                args=list(args),
            )
        except OSError as exc:
            return GhResult(UNRUN, stderr=f"`gh` could not be spawned: {exc}", args=list(args))
        return GhResult(proc.returncode, proc.stdout, proc.stderr, list(args))


class FakeGh:
    """Scripted ``gh`` for tests: canned answers by command prefix, every
    invocation recorded — the FakeDriver pattern at the GitHub boundary.

    Keys are the leading words of a command (``"repo view"``, ``"label
    list"``); the longest match wins, and an unscripted command succeeds
    silently, so a test scripts only what it cares about.
    """

    def __init__(self, results: dict[str, GhResult] | None = None):
        self.results = dict(results or {})
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> GhResult:
        self.calls.append(list(args))
        joined = " ".join(args)
        for key in sorted(self.results, key=len, reverse=True):
            if joined.startswith(key):
                return replace(self.results[key], args=list(args))
        return GhResult(0, args=list(args))


# -- where the repository and the token come from ----------------------------


def parse_repo(remote_url: str) -> str:
    """``owner/name`` from a remote URL, or "" when it is not a GitHub one."""
    match = GITHUB_REMOTE.search(remote_url.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` lines from a git-ignored env file. A missing or unreadable
    file is the ordinary case, not an error."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_token(root: Path) -> tuple[str, str]:
    """The GitHub token and where it came from: the project's git-ignored env
    file first, then this process's environment, else ``gh``'s own login.

    Read from the invoking checkout, which is the only place that file exists:
    loops are worktree-resident, so no worker's filesystem view holds it.
    """
    token = read_env_file(root / ENV_FILE).get(TOKEN_VAR, "")
    if token:
        return token, f"{ENV_FILE} ({TOKEN_VAR})"
    if os.environ.get(TOKEN_VAR):
        return os.environ[TOKEN_VAR], f"environment ({TOKEN_VAR})"
    return "", "gh login"


# -- the desired surface (pure: a function of the store alone) ----------------


def spec_labels(state: str) -> set[str]:
    """The labels a Spec's tracking issue should carry in this state."""
    labels = {"giro:spec"}
    if f"giro:{state}" in SPEC_LABELS:
        labels.add(f"giro:{state}")
    return labels


def spec_marker(slug: str) -> str:
    """What a Spec's tracking issue is known by when nothing points at it."""
    return f"giro:spec:{slug}"


def issue_marker(slug: str, issue_id: str) -> str:
    """The same, for one giro Issue's sub-issue."""
    return f"giro:issue:{slug}/{issue_id}"


def marked_bodies(rows: Any) -> dict[str, int]:
    """The giro-generated issues among what a repository holds, by marker: the
    mapping recovered from GitHub when the store has lost it. An issue with no
    marker is a human's and is not in here."""
    found: dict[str, int] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        number = int(row.get("number", 0) or 0)
        match = BODY_MARKER.search(str(row.get("body") or ""))
        if number and match:
            found.setdefault(match.group(1), number)
    return found


def parent_body(spec: Spec) -> str:
    """The tracking issue's body — a pointer, not a copy: the Spec's markdown
    stays the memory, and this issue closes when the human merges."""
    return "\n".join(
        [
            f"Tracking issue for giro Spec `{spec.slug}`.",
            "",
            f"Source of truth: `docs/specs/{spec.slug}/SPEC.md` on branch "
            f"`giro/{spec.slug}`. The pull request carries the Issue checklist and "
            "closes this issue when you merge it.",
            "",
            f"<!-- {spec_marker(spec.slug)} -->",
        ]
    )


def issue_labels(issue: Issue) -> set[str]:
    """The labels a sub-issue should carry for this Issue: what it is, where
    the loop has it, and — for a Validate finding — where it came from."""
    labels = {"giro:issue", f"giro:{issue.state}"}
    if issue.gap_gate:
        labels.add("giro:gap")
    return labels & {*ISSUE_LABELS}


def issue_body(spec: Spec, issue: Issue, blockers: list[str] | None = None) -> str:
    """The sub-issue's body — a pointer, plus whatever GitHub cannot show
    natively: the gate that filed a gap Issue, and the blocking edges when this
    repository's API has no issue dependencies to hang them on."""
    lines = [f"giro Issue `{issue.id}` of Spec `{spec.slug}`.", ""]
    if issue.gap_gate:
        lines += [
            f"A gap finding, filed by the `{issue.gap_gate}` validate gate.",
            "",
        ]
    if blockers:
        lines += [f"Blocked by {', '.join(blockers)}.", ""]
    lines += [
        f"Source of truth: `docs/specs/{spec.slug}/issues/{issue.id}.md` on branch "
        f"`giro/{spec.slug}`. The engine writes the state; edits here are display "
        "only and are overwritten at the next projection.",
        "",
        f"<!-- {issue_marker(spec.slug, issue.id)} -->",
    ]
    return "\n".join(lines)


def marked(key: str, body: str) -> str:
    """A generated comment: what it says, and the marker that makes it findable
    again — the whole of its identity, so nothing is ever posted twice."""
    return f"{body.rstrip()}\n\n<!-- giro:comment:{key} -->"


def log_comments(spec: Spec, issue: Issue) -> list[tuple[str, str]]:
    """The comments an Issue's story should be: one per appended log section,
    verbatim — one writer, one story, two displays. Keyed by position, because
    the log is append-only and a section never moves."""
    comments = []
    for n, section in enumerate(log_sections(issue.body), 1):
        key = f"{spec.slug}/{issue.id}#{n}"
        comments.append((key, marked(key, section)))
    return comments


def note_comment(heading: str, lines: list[str]) -> str:
    """One orchestration moment, rendered: what happened, and what it was
    about. Engine-observed facts only — a worker's own words stay in the
    attempt comment the log gave them."""
    if not lines:
        return f"## {heading}"
    return "\n".join([f"## {heading}", "", *(f"- {line}" for line in lines)])


def escalation_comment(
    target: str, reason: str, lines: list[str] | None = None, assignee: str = ""
) -> str:
    """The escalation, said where a human will feel it: the durable reason the
    log already holds, whatever the last gates found, and the one act that
    answers it — re-invoking giro. The mention is the notification."""
    out = [f"## Needs human — `{target}`", ""]
    if assignee:
        out += [f"@{assignee} — the loop stopped here and needs a decision.", ""]
    out.append(reason.strip() or "no reason recorded")
    if lines:
        out += ["", *(f"- {line}" for line in lines)]
    out += [
        "",
        f"Answer it in the markdown, then re-invoke `giro implement {target}` — the "
        "re-invocation *is* the answer, and the budget resets.",
    ]
    return "\n".join(out)


def resumed_comment(target: str) -> str:
    """The other half of an escalation: the answer arrived and the loop moved."""
    return "\n".join(
        [
            f"## Run resumed — `{target}`",
            "",
            "A human re-invoked giro: the escalation is cleared and the loop is "
            "running again from where it stopped.",
        ]
    )


def completion_comment(
    spec: Spec, issues: list[Issue], pr: int = 0, ready: bool = False
) -> str:
    """The last word on the parent: every Issue terminal, the Spec-level gates
    green, and the one act still outstanding — which is a human's (ADR-0005).

    ``ready`` is whether the draft→ready flip actually landed: on a degraded
    network it can fail soft, and the comment must not claim a review-ready pull
    request that is in fact still a draft (``giro project`` reconciles it later)."""
    out = [
        f"## Validate passed — `{spec.slug}` is done",
        "",
        "Every Issue is terminal and the Spec-level gates are green.",
    ]
    if pr and ready:
        out.append(f"Pull request #{pr} is out of draft and ready for review.")
    elif pr:
        out.append(
            f"Pull request #{pr} could not be flipped out of draft (projection "
            "degraded) — run `giro project` to reconcile it."
        )
    if issues:
        out += ["", *(f"- `{i.id}` {i.title or i.id} — {i.state}" for i in issues)]
    out += [
        "",
        "Nothing merges automatically: the merge is yours, and merging closes this issue.",
    ]
    return "\n".join(out)


def projected_comments(rows: Any) -> dict[str, tuple[int, str]]:
    """The generated comments among what GitHub holds, by key: their id and
    what they currently say. A comment with no marker is a human's and is not
    in here — so it is never edited and never counted."""
    held: dict[str, tuple[int, str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        body = str(row.get("body") or "")
        if match := COMMENT_MARKER.search(body):
            held[match.group(1)] = (int(row.get("id", 0) or 0), body)
    return held


def _is_write(args: list[str]) -> bool:
    """Whether an invocation creates content — the calls a secondary rate limit
    is aimed at, and the only ones worth asking twice."""
    return _verbed(args, WRITE_VERBS)


def _is_change(args: list[str]) -> bool:
    """Whether an invocation changes what a human would see — what a Reconcile
    reports. A separate question from :func:`_is_write`, deliberately: every
    content-creating write is a change, and so is the flip out of draft, which
    creates nothing at all."""
    return _verbed(args, CHANGE_VERBS)


def _verbed(args: list[str], verbs: frozenset[str]) -> bool:
    if "-X" in args:
        method = args[args.index("-X") + 1 :]
        return bool(method) and method[0] in WRITE_METHODS
    return len(args) > 1 and args[1] in verbs


@dataclass
class Change:
    """One write a convergence made — or one thing it deliberately did not do."""

    kind: str  # "created" | "updated" | "skipped" | "failed"
    what: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "what": self.what}

    def __str__(self) -> str:
        return f"{self.kind:<7} {self.what}"


def _describe_change(args: list[str]) -> tuple[str, str]:
    """What one change did, said the way a human reads it: ``created`` when it
    makes a thing, ``updated`` when it corrects one that was already there."""
    head = args[:2]
    if head == ["issue", "create"]:
        return "created", "tracking issue"
    if head == ["pr", "create"]:
        return "created", "draft pull request"
    if head == ["label", "create"]:
        return "created", f"label {args[2]}"
    if head == ["issue", "edit"]:
        return "updated", f"issue #{args[2]} ({_edited(args)})"
    if head == ["issue", "close"]:
        return "updated", f"issue #{args[2]} closed"
    if head == ["issue", "reopen"]:
        return "updated", f"issue #{args[2]} reopened"
    if head == ["pr", "edit"]:
        return "updated", f"pull request #{args[2]} body"
    if head == ["pr", "ready"]:
        return "updated", f"pull request #{args[2]} ready for review"
    path = args[1] if len(args) > 1 else ""
    endpoint = path.partition("?")[0]
    if endpoint.endswith("/issues"):
        return "created", "sub-issue"
    if endpoint.endswith("/sub_issues"):
        return "updated", "sub-issue link"
    if endpoint.endswith("/blocked_by"):
        return "updated", "blocking edge"
    if endpoint.endswith("/comments"):
        return "created", "comment"
    if "/comments/" in endpoint:
        return "updated", "comment"
    if "/statuses/" in endpoint:
        return "updated", "commit status"
    return "updated", " ".join(args)[:80]


def _describe_read(args: list[str]) -> str:
    """What one read was asking GitHub for, named the way a human reads it.

    A read is how a convergence learns what to write: one that fails leaves the
    surface short of the store, so it is reported as the hole it is rather than
    swallowed. The phrase reads after "read of".
    """
    head = args[:2]
    if head == ["issue", "view"] and len(args) > 2:
        return f"issue #{args[2]}"
    if head == ["pr", "view"] and len(args) > 2:
        return f"pull request #{args[2]}"
    if head == ["issue", "list"]:
        return f"the {_flagged(args, '--label') or 'giro'} issue index"
    if head == ["pr", "list"]:
        return f"the pull request for {_flagged(args, '--head')}"
    endpoint = (args[1] if len(args) > 1 else "").partition("?")[0]
    if found := re.search(r"/issues/(\d+)(?:/([\w/]+))?$", endpoint):
        number, tail = found.group(1), found.group(2) or ""
        named = {
            "": f"issue #{number}",
            "comments": f"comments on issue #{number}",
            "sub_issues": f"the sub-issues of #{number}",
        }
        return named.get(tail, f"{tail} of issue #{number}")
    return " ".join(args)[:80]


def _flagged(args: list[str], name: str) -> str:
    """What a flag was given, for naming a call — not for deciding anything."""
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return ""


def _is_probe(args: list[str]) -> bool:
    """Whether a read is asking *whether GitHub does this at all*.

    One read is a feature probe rather than a fetch: a repository whose API has
    no issue dependencies refuses the ``blocked_by`` endpoint on every single
    run, and the edge is then stated in the sub-issue body instead. Nothing is
    missing from the surface, so nothing is reported.
    """
    endpoint = (args[1] if len(args) > 1 else "").partition("?")[0]
    return args[:1] == ["api"] and endpoint.endswith("/dependencies/blocked_by")


def _edited(args: list[str]) -> str:
    """Which of an ``issue edit``'s hands were used — labels, body, assignee."""
    names = {
        "--add-label": "labels",
        "--remove-label": "labels",
        "--body": "body",
        "--add-assignee": "assignee",
    }
    used = [name for flag, name in names.items() if flag in args]
    return ", ".join(dict.fromkeys(used)) or "edited"


def _created_number(args: list[str], result: GhResult) -> int:
    """The number a creating call minted, wherever it prints it: `gh issue
    create` says a URL, the REST endpoint answers an object."""
    if args[:2] in (["issue", "create"], ["pr", "create"]):
        return _number(result.stdout)
    body = result.json()
    return int(body.get("number", 0) or 0) if isinstance(body, dict) else 0


class TallyGh:
    """A ``gh`` that says what passed through it.

    Reconcile reports what it created and what it only corrected — and the
    writes the convergence makes *are* that report, so it is written here at
    the boundary rather than described a second time somewhere that could
    drift from it. A read that *lands* changes nothing and is not counted; a
    read that fails is counted while ``watch_reads`` is on, because every write
    here is decided by a read and a convergence that could not look is a
    convergence that quietly stopped. Fail-soft is not permission to claim a
    convergence that did not happen.

    A write the secondary rate limit refused and the retry then landed is one
    logical write, not a failure and a success: that limit is GitHub asking to
    be asked again, and a convergence that fully landed must not report a
    failure it recovered from.
    """

    def __init__(self, gh: GhRunner):
        self.gh = gh
        self.changes: list[Change] = []
        # Whether a failed read counts as a gap. Off through preflight, whose
        # reads answer for themselves in its own checks; on for the convergence
        # they precede.
        self.watch_reads = False
        # The invocation the last call was told to sit out, and the failure
        # recorded for it — the one Change a retry may supersede.
        self._paused: tuple[list[str], Change] | None = None

    def run(self, args: list[str]) -> GhResult:
        result = self.gh.run(args)
        paused, self._paused = self._paused, None
        if not _is_change(args):
            if self.watch_reads and not result.ok and not _is_probe(args):
                self.changes.append(
                    Change("failed", f"read of {_describe_read(args)}: {result.error}")
                )
            return result
        kind, what = _describe_change(args)
        if not result.ok:
            refused = Change("failed", f"{what}: {result.error}")
            self.changes.append(refused)
            if SECONDARY_LIMIT.search(result.error):
                self._paused = (list(args), refused)
            return result
        if paused and paused[0] == args and self.changes and self.changes[-1] is paused[1]:
            self.changes.pop()  # the pause worked: the refusal it supersedes goes
        number = _created_number(args, result)
        self.changes.append(Change(kind, f"{what} #{number}" if number else what))
        return result

    def take(self) -> list[Change]:
        """The changes since the last time anyone asked."""
        changes, self.changes = self.changes, []
        return changes


@dataclass
class IssueSnapshot:
    """What GitHub currently holds for one issue — read to diff against, never
    to decide with (ADR-0014)."""

    labels: set[str] = field(default_factory=set)
    state: str = ""  # "open" | "closed"
    reason: str = ""  # "completed" | "not_planned" | ""
    body: str = ""


def snapshot_of(data: Any) -> IssueSnapshot | None:
    """One issue's answer, however it was asked for: ``gh issue view`` shouts
    its states and ``gh api`` spells its keys with underscores."""
    if not isinstance(data, dict):
        return None
    rows = data.get("labels", [])
    return IssueSnapshot(
        labels={
            row["name"]
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and "name" in row
        },
        state=str(data.get("state", "")).lower(),
        reason=_reason(data.get("stateReason") or data.get("state_reason") or ""),
        body=str(data.get("body") or ""),
    )


def _reason(value: Any) -> str:
    return str(value).lower().replace(" ", "_")


def render_zone(spec: Spec, issues: list[Issue], parent: int = 0, phase: str = "") -> str:
    """The engine zone of a Spec's pull-request body: where the Run is, and
    every Issue with its state — the store, rendered."""
    lines = [ZONE_START, "", f"### giro — `{spec.slug}`", ""]
    if parent:
        lines.append(f"Closes #{parent}")  # the human's merge closes the parent
        lines.append("")
    where = f"**{spec.state}**" + (f" — {phase}" if phase else "")
    lines.append(f"- state: {where}")
    if spec.base_branch:
        lines.append(f"- base branch: `{spec.base_branch}`")
    lines.append("")
    if issues:
        lines.append("**Issues**")
        lines.append("")
        for issue in issues:
            mark = "x" if issue.state in ISSUE_TERMINAL else " "
            title = issue.title or issue.id
            lines.append(f"- [{mark}] `{issue.id}` {title} — {issue.state}")
    else:
        lines.append("_no Issues yet._")
    lines += [
        "",
        "<sub>Rendered by giro from the markdown store; edits inside this block are "
        "overwritten. Nothing merges automatically.</sub>",
        ZONE_END,
    ]
    return "\n".join(lines)


def splice_zone(body: str, zone: str) -> str:
    """``zone`` put into ``body`` where the fence is, appended where there is
    none — and human prose outside the fence carried through untouched."""
    start = body.find(ZONE_START)
    end = body.find(ZONE_END)
    if start == -1 or end == -1 or end < start:
        prose = body.rstrip()
        return f"{prose}\n\n{zone}\n" if prose else f"{zone}\n"
    return body[:start] + zone + body[end + len(ZONE_END) :]


def _number(text: str) -> int:
    """The issue or pull-request number in the URL ``gh`` prints on create."""
    match = ISSUE_NUMBER.search(text)
    return int(match.group(1)) if match else 0


@dataclass
class SpecSurface:
    """What a Spec is on GitHub: its tracking issue, its pull request, and one
    sub-issue per giro Issue. 0 — or an absent key — means "not there yet";
    these are the identifiers the engine records in frontmatter, so every later
    Run finds the surface instead of remaking it."""

    issue: int = 0
    pr: int = 0
    issues: dict[str, int] = field(default_factory=dict)  # issue id -> sub-issue number


# -- preflight ---------------------------------------------------------------


@dataclass
class Check:
    name: str
    state: str  # "ok" | "failed" | "skipped"
    detail: str = ""


# What a token can render but never make ring. GitHub suppresses notifications
# for one's own actions, so a token owned by the human it escalates to assigns
# and mentions them in silence — the one failure mode a green preflight can
# still hide.
SELF_NOTIFICATION = (
    "the token's identity is @{login}, the same human giro assigns and mentions — GitHub "
    "never notifies anyone about their own actions, so an escalation will render and "
    "reach no one. Use a token owned by a machine account (fine-grained, this repository "
    "only: issues, pull requests, contents, commit statuses — read/write)."
)
NO_ASSIGNEE = (
    "no assignee configured, so needs-human notifies nobody — set assignee in "
    "[github_projection] of giro.toml to the human giro should reach"
)


@dataclass
class PreflightReport:
    """Whether a repository is ready to be projected onto, and why not."""

    repo: str
    token_source: str
    enabled: bool
    checks: list[Check] = field(default_factory=list)
    # What is usable but will not notify — said out loud, never fatal: a
    # notification nobody receives is not a reason to refuse a Run.
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(check.state == "ok" for check in self.checks)

    @property
    def failure(self) -> str:
        """The first thing that went wrong — what the ProjectionError names."""
        return next(
            (f"{c.name}: {c.detail}" for c in self.checks if c.state == "failed"), ""
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "enabled": self.enabled,
            "repo": self.repo,
            "token_source": self.token_source,
            "checks": [
                {"name": c.name, "state": c.state, "detail": c.detail} for c in self.checks
            ],
            "warnings": list(self.warnings),
        }

    def lines(self) -> list[str]:
        marks = {"ok": "✓", "failed": "✗", "skipped": "-"}
        out = [
            f"repo {self.repo or '(none)'} — token from {self.token_source}",
            *(
                f"  {marks.get(c.state, '?')} {c.name}: {c.detail or c.state}"
                for c in self.checks
            ),
            *(f"  ! {warning}" for warning in self.warnings),
        ]
        if self.ready and self.enabled:
            out.append("ready — Projection will render this Run onto GitHub")
        elif self.ready:
            out.append(
                "the repository is ready, but Projection is off — set enabled = true "
                "in [github_projection] of giro.toml"
            )
        else:
            out.append(f"not ready — {self.failure}")
        return out


@dataclass
class Projection:
    """The engine's one-way hands on GitHub, fail-soft by construction.

    Only the engine's main thread holds one: serialization is the parallel-wave
    invariant and the rate discipline at once.
    """

    settings: ProjectionSettings = field(default_factory=ProjectionSettings)
    gh: GhRunner | None = None
    repo: str = ""
    token_source: str = "gh login"
    ledger: Ledger = field(default_factory=Ledger)  # the Run nobody is watching
    # Whether this repository's API offers native issue dependencies. Assumed
    # until it refuses once, and then stated in prose for the rest of the Run.
    dependencies: bool = True
    login: str = ""  # who the token is, learned at preflight — for the warning alone
    # issue number -> the database id the sub-issue and dependency endpoints
    # take, learned as issues are created or looked up.
    ids: dict[int, int] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.gh is not None)

    def attach(self, ledger: Ledger) -> None:
        """The Run's Ledger, once it exists — where failures are recorded."""
        self.ledger = ledger

    # -- the boundary ---------------------------------------------------------

    def call(self, *args: str, action: str = "") -> GhResult:
        """One ``gh`` invocation. A failure is recorded and returned, never
        raised: the loop's outcome must not depend on a tracker.

        A content-creating call that meets a secondary rate limit waits and
        asks once more — that limit is GitHub asking for a pause, and the pause
        is what serialization already gives every other call. A second refusal
        drops fail-soft like any other.
        """
        if not self.enabled or self.gh is None:
            return GhResult(OFF, stderr="projection is off", args=list(args))
        result = self.gh.run(list(args))
        retried = False
        if not result.ok and _is_write(list(args)) and SECONDARY_LIMIT.search(result.error):
            time.sleep(RETRY_PAUSE)
            result = self.gh.run(list(args))
            retried = True
        if not result.ok:
            self.ledger.event(
                "projection",
                action=action or args[0],
                command=" ".join(args)[:200],
                ok=False,
                detail=result.error + (" — after one retry" if retried else ""),
            )
        return result

    def note(self, action: str, ok: bool, detail: str = "") -> None:
        """Record a projection moment that was not one ``gh`` call — a push, a
        surface created. Observability only; nothing reads it back."""
        self.ledger.event("projection", action=action, ok=ok, detail=detail)

    # -- the Spec's surface ---------------------------------------------------

    def project_spec(
        self,
        spec: Spec,
        issues: list[Issue],
        phase: str = "",
        branch_published: bool = True,
    ) -> SpecSurface:
        """Render one Spec onto GitHub and answer with its identifiers.

        Find-or-create through what frontmatter records, so a first activation
        creates the tracking issue and the draft pull request exactly once and
        every later Run finds them. Everything else is compared before it is
        written: projecting twice changes nothing.

        ``branch_published`` says whether ``giro/<slug>`` is on the remote — a
        pull request cannot be opened for a head that is not there yet, so an
        unpushed branch simply waits for the next checkpoint.
        """
        surface = SpecSurface(spec.github_issue, spec.github_pr)
        if not self.enabled:
            return surface
        if not surface.issue:
            surface.issue = self._create_parent(spec)
        if not surface.issue:
            return surface  # nothing to hang labels, sub-issues, or a keyword on
        self._apply_labels(surface.issue, spec)
        surface.issues = self.project_issues(spec, issues, surface.issue)
        zone = render_zone(spec, issues, surface.issue, phase)
        if not surface.pr:
            if branch_published:
                surface.pr = self._create_pull_request(spec, zone)
        else:
            self._update_pull_request(surface.pr, zone)
        return surface

    def discover(self, spec: Spec, issues: list[Issue]) -> SpecSurface:
        """The surface a Spec already has, found without frontmatter pointing at it.

        Every generated body carries a hidden marker, so the mapping can be
        recovered from GitHub itself when the store no longer holds it — a
        clone that never ran the Run, an outage that swallowed the identifier
        commit, a Reconcile that must not write the store to remember what it
        just made. Read to *find*, never to decide (ADR-0014): what the surface
        should then say still comes from the markdown alone.

        Frontmatter wins wherever it speaks, and a repository that already
        records every identifier is never scanned at all.
        """
        surface = SpecSurface(
            spec.github_issue,
            spec.github_pr,
            {i.id: i.github_issue for i in issues if i.github_issue},
        )
        if not self.enabled:
            return surface
        if not surface.issue or len(surface.issues) < len(issues):
            found = self._marked()
            surface.issue = surface.issue or found.get(spec_marker(spec.slug), 0)
            for issue in issues:
                if not surface.issues.get(issue.id):
                    number = found.get(issue_marker(spec.slug, issue.id), 0)
                    if number:
                        surface.issues[issue.id] = number
        if not surface.pr:
            surface.pr = self._find_pull_request(spec.slug)
        return surface

    def _marked(self) -> dict[str, int]:
        """Every giro-generated issue this repository holds, by marker.

        Narrowed to the two labels the engine puts on what it makes — a parent
        wears ``giro:spec``, a sub-issue ``giro:issue``, and preflight ensures
        both exist — because the scan is bounded and an unfiltered window has a
        cliff: on a repository busier than one page, giro's own issues fall
        outside it and the surface gets built a second time, every time.

        What this cannot find is an issue a hand stripped the kind label off
        whose number the store no longer records either — rare, and the label
        is put back by ordinary convergence wherever the mapping still stands.
        """
        found: dict[str, int] = {}
        for label in ("giro:spec", "giro:issue"):
            listed = self.call(
                "issue", "list",
                "--repo", self.repo,
                "--label", label,
                "--state", "all",
                "--json", "number,body",
                "--limit", str(DISCOVER_LIMIT),
                action="discover",
            )
            if listed.ok:
                found |= marked_bodies(listed.json())
        return found

    def _find_pull_request(self, slug: str) -> int:
        """The pull request already open — or already closed — for a Spec's own
        head branch. The oldest wins: the one the Spec's first checkpoint made."""
        listed = self.call(
            "pr", "list",
            "--repo", self.repo,
            "--head", f"giro/{slug}",
            "--state", "all",
            "--json", "number",
            "--limit", "20",
            action="discover",
        )
        rows = listed.json() if listed.ok else []
        numbers = [
            int(row.get("number", 0) or 0)
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        ]
        return min((n for n in numbers if n), default=0)

    def _create_parent(self, spec: Spec) -> int:
        result = self.call(
            "issue", "create",
            "--repo", self.repo,
            "--title", spec.title or spec.slug,
            "--body", parent_body(spec),
            "--label", "giro:spec",
            action="spec-issue",
        )
        number = _number(result.stdout) if result.ok else 0
        if number:
            self.note("spec-issue", ok=True, detail=f"{self.repo}#{number} tracks {spec.slug}")
        return number

    def _apply_labels(self, parent: int, spec: Spec) -> None:
        """The Spec's state on the parent issue — added and removed, never
        closed: ``done`` leaves it open for the human's merge to close."""
        desired = spec_labels(spec.state)
        result = self.call(
            "issue", "view", str(parent), "--repo", self.repo, "--json", "labels",
            action="spec-labels",
        )
        if not result.ok:
            return
        viewed = result.json()
        rows = viewed.get("labels", []) if isinstance(viewed, dict) else []
        current = {
            row["name"] for row in rows if isinstance(row, dict) and "name" in row
        }
        add = sorted(desired - current)
        remove = sorted(current & (set(SPEC_LABELS) - desired))
        if not add and not remove:
            return
        args = ["issue", "edit", str(parent), "--repo", self.repo]
        if add:
            args += ["--add-label", ",".join(add)]
        if remove:
            args += ["--remove-label", ",".join(remove)]
        self.call(*args, action="spec-labels")

    def _create_pull_request(self, spec: Spec, zone: str) -> int:
        """The Spec's review surface, opened as a draft from its first
        checkpoint — draft until Validate says otherwise, merged by a human."""
        args = [
            "pr", "create",
            "--repo", self.repo,
            "--head", f"giro/{spec.slug}",
            "--draft",
            "--title", spec.title or spec.slug,
            "--body", splice_zone("", zone),
        ]
        if spec.base_branch:
            args += ["--base", spec.base_branch]
        result = self.call(*args, action="spec-pr")
        number = _number(result.stdout) if result.ok else 0
        if number:
            self.note("spec-pr", ok=True, detail=f"draft {self.repo}#{number} for {spec.slug}")
        return number

    def _update_pull_request(self, pr: int, zone: str) -> None:
        """Re-render the engine zone in place. The body is read to splice it —
        content, never control (ADR-0014) — so prose outside the fence stays
        exactly as the human left it, and an unchanged body is not rewritten."""
        result = self.call(
            "pr", "view", str(pr), "--repo", self.repo, "--json", "body", action="spec-pr"
        )
        if not result.ok:
            return
        viewed = result.json()
        body = viewed.get("body", "") if isinstance(viewed, dict) else ""
        updated = splice_zone(body or "", zone)
        if updated == body:
            return
        self.call(
            "pr", "edit", str(pr), "--repo", self.repo, "--body", updated, action="spec-pr"
        )

    # -- the Issues' surface --------------------------------------------------

    def project_issues(self, spec: Spec, issues: list[Issue], parent: int) -> dict[str, int]:
        """Every giro Issue as a sub-issue of the parent, in the state the store
        gives it — and the numbers, for the engine to record in frontmatter.

        Four passes, because a blocking edge needs both of its ends projected:
        create what is missing, hang every sub-issue under the parent, hang the
        blocking edges, then converge labels, body, and open/closed. What GitHub
        holds is read to diff against, so a hand-closed or relabelled sub-issue
        is simply restored here and changes nothing anywhere else.
        """
        if not self.enabled or not parent:
            return {}
        numbers = {i.id: i.github_issue for i in issues if i.github_issue}
        fresh: dict[str, IssueSnapshot | None] = {}
        for issue in issues:
            if issue.id in numbers:
                continue
            number, snapshot = self._create_sub_issue(spec, issue)
            if number:
                numbers[issue.id] = number
                fresh[issue.id] = snapshot
        self._hang_under_parent(parent, numbers)
        stated = {issue.id: self._link_blockers(issue, numbers) for issue in issues}
        for issue in issues:
            if number := numbers.get(issue.id, 0):
                self._converge_issue(spec, issue, number, stated[issue.id], fresh.get(issue.id))
        return numbers

    def _create_sub_issue(self, spec: Spec, issue: Issue) -> tuple[int, IssueSnapshot | None]:
        """One sub-issue, made through the endpoint that answers with both of
        its identifiers: the number a human reads and the id GitHub's own
        sub-issue link takes. Created once — the number goes to frontmatter,
        and hanging it under the parent is the converged pass's job."""
        args = [
            "api", f"repos/{self.repo}/issues",
            "-X", "POST",
            "-f", f"title={issue.title or issue.id}",
            "-f", f"body={issue_body(spec, issue)}",
        ]
        for label in sorted(issue_labels(issue)):
            args += ["-f", f"labels[]={label}"]
        result = self.call(*args, action="sub-issue")
        created = result.json() if result.ok else None
        if not isinstance(created, dict):
            return 0, None
        number = int(created.get("number", 0) or 0)
        if not number:
            return 0, None
        if rest_id := int(created.get("id", 0) or 0):
            self.ids[number] = rest_id
        self.note("sub-issue", ok=True, detail=f"{self.repo}#{number} is {issue.ref}")
        return number, snapshot_of(created)

    def _hang_under_parent(self, parent: int, numbers: dict[str, int]) -> None:
        """Every recorded sub-issue under the parent — the progress bar itself,
        converged like everything else here rather than written once.

        The link is a write of its own and fails soft like any other, so a
        sub-issue can end up created, recorded in frontmatter, and never hung —
        and the number in frontmatter is exactly what keeps a later projection
        from creating (and linking) it again. So what the parent holds is read
        once per projection and the links it is missing are made, then or later.
        """
        if not numbers:
            return
        endpoint = f"repos/{self.repo}/issues/{parent}/sub_issues"
        # The page size rides in the path: a ``-F`` parameter would make `gh
        # api` POST it, and this call is the read the writes are diffed against.
        listed = self.call("api", f"{endpoint}?per_page=100", action="sub-issue")
        if not listed.ok:
            return
        rows = listed.json()
        hung = {
            int(row.get("number", 0) or 0)
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        }
        for number in sorted(set(numbers.values()) - hung):
            if rest_id := self._rest_id(number):
                self.call(
                    "api", endpoint,
                    "-X", "POST",
                    "-F", f"sub_issue_id={rest_id}",
                    action="sub-issue",
                )

    def _link_blockers(self, issue: Issue, numbers: dict[str, int]) -> list[str]:
        """A giro blocking edge, put where GitHub will take it.

        Native issue dependencies where the API offers them; otherwise the edge
        is stated in the sub-issue body, and the refs returned here are the ones
        that states — every edge GitHub would not take, including one whose
        blocker it does not hold at all. One refusal settles the endpoint for
        the rest of the Run; either way the edge shows somewhere.
        """
        if not issue.blocked_by:
            return []
        refs = [f"#{numbers[d]}" if numbers.get(d) else f"`{d}`" for d in issue.blocked_by]
        number = numbers.get(issue.id, 0)
        if not number or not self.dependencies:
            return refs
        endpoint = f"repos/{self.repo}/issues/{number}/dependencies/blocked_by"
        listed = self.call("api", endpoint, action="blocked-by")
        if not listed.ok:
            self.dependencies = False
            return refs
        rows = listed.json()
        linked = {
            int(row.get("number", 0) or 0)
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        }
        hung: set[str] = set()
        for dep in issue.blocked_by:
            blocker = numbers.get(dep, 0)
            if blocker and blocker in linked:
                hung.add(dep)
                continue
            blocker_id = self._rest_id(blocker) if blocker else 0
            if not blocker_id:
                continue  # no end to hang it on — the body states this one
            added = self.call(
                "api", endpoint, "-X", "POST", "-F", f"issue_id={blocker_id}",
                action="blocked-by",
            )
            if not added.ok:
                self.dependencies = False
                return refs
            hung.add(dep)
        return [
            ref for dep, ref in zip(issue.blocked_by, refs, strict=True) if dep not in hung
        ]

    def _rest_id(self, number: int) -> int:
        """The database id behind an issue number. Asked once per Run: a
        number's id never changes under it."""
        if number not in self.ids:
            result = self.call(
                "api", f"repos/{self.repo}/issues/{number}", "--jq", ".id", action="sub-issue"
            )
            digits = result.stdout.strip()
            self.ids[number] = int(digits) if result.ok and digits.isdigit() else 0
        return self.ids[number]

    def _converge_issue(
        self,
        spec: Spec,
        issue: Issue,
        number: int,
        blockers: list[str],
        known: IssueSnapshot | None,
    ) -> None:
        """One sub-issue brought back to what the store says: its labels, its
        body, open or closed for the right reason, and the Issue's story as
        comments. A sub-issue just created answers for itself, so its first
        projection reads nothing back."""
        snapshot = known or self._read_issue(number)
        if snapshot is None:
            return
        desired = issue_labels(issue)
        changes: list[str] = []
        if add := sorted(desired - snapshot.labels):
            changes += ["--add-label", ",".join(add)]
        if remove := sorted(snapshot.labels & ({*ISSUE_LABELS} - desired)):
            changes += ["--remove-label", ",".join(remove)]
        body = issue_body(spec, issue, blockers)
        if body != snapshot.body:
            changes += ["--body", body]
        if changes:
            self.call(
                "issue", "edit", str(number), "--repo", self.repo, *changes, action="issue-state"
            )
        self._converge_open_state(issue, number, snapshot)
        self._project_log(spec, issue, number, fresh=known is not None)

    def _converge_open_state(self, issue: Issue, number: int, snapshot: IssueSnapshot) -> None:
        """done closes as completed, wontfix as not planned, everything else is
        open — including a sub-issue a human closed while the loop still has
        work to do."""
        reason = CLOSE_REASON.get(issue.state, "")
        if not reason:
            if snapshot.state == "closed":
                self.call(
                    "issue", "reopen", str(number), "--repo", self.repo, action="issue-state"
                )
            return
        if snapshot.state == "closed" and snapshot.reason == _reason(reason):
            return
        self.call(
            "issue", "close", str(number),
            "--repo", self.repo,
            "--reason", reason,
            action="issue-state",
        )

    def _read_issue(self, number: int) -> IssueSnapshot | None:
        result = self.call(
            "issue", "view", str(number),
            "--repo", self.repo,
            "--json", "labels,state,stateReason,body",
            action="issue-state",
        )
        return snapshot_of(result.json()) if result.ok else None

    # -- the narration --------------------------------------------------------

    def narrate(
        self, spec: Spec, key: str, heading: str, lines: list[str] | None = None
    ) -> None:
        """One orchestration moment — a plan, a wave, a Validate verdict, a gap
        cycle — on the Spec's tracking issue, where the whole Run is one thread.

        ``key`` names the moment within this Run, so re-projecting it edits the
        comment it already posted; a later Run's wave 1 is a moment of its own
        and gets a comment of its own.
        """
        if not self.enabled or not spec.github_issue:
            return
        moment = self._key(spec.slug, key)
        self._moment(spec.github_issue, moment, note_comment(heading, lines or []))

    def _key(self, target: str, moment: str) -> str:
        """One moment of one Run, named: re-projecting it converges the comment
        it already posted, and a later Run's same moment is a moment of its
        own."""
        return f"{self.ledger.id or 'run'}:{target}:{moment}"

    def _moment(self, number: int, key: str, body: str) -> None:
        """One generated comment on one issue, posted once and converged ever
        after — or not written at all, when GitHub will not say what it holds."""
        held = self._held_comments(number)
        if held is None:
            return
        self._write_comment(number, key, marked(key, body), held)

    def report_gates(
        self,
        sha: str,
        kind: str,
        verdicts: dict[str, str],
        findings: list[Finding] | None = None,
    ) -> None:
        """The gate set's verdicts as commit statuses on a published commit —
        one per gate, so "all gates green" is literally green on the pull
        request, and a gate that said no says why in its description."""
        if not self.enabled or not sha or not verdicts:
            return
        why: dict[str, str] = {}
        for finding in findings or []:
            why.setdefault(finding.gate, finding.summary)
        for gate, verdict in verdicts.items():
            state = STATUS_STATE.get(verdict, "failure")
            description = why.get(gate) or f"{kind} gate {gate}: {verdict}"
            self.call(
                "api", f"repos/{self.repo}/statuses/{sha}",
                "-X", "POST",
                "-f", f"state={state}",
                "-f", f"context=giro/{kind}/{gate}",
                "-f", f"description={description[:140]}",
                action="status",
            )

    def _project_log(self, spec: Spec, issue: Issue, number: int, fresh: bool = False) -> None:
        """The Issue's story on its sub-issue: every appended log section as one
        comment, posted when it appears and edited back into shape if a hand
        changes it. A sub-issue created moments ago holds nothing yet, so its
        first projection reads nothing back."""
        desired = log_comments(spec, issue)
        if not desired:
            return
        held = {} if fresh else self._held_comments(number)
        if held is None:
            return
        for key, body in desired:
            self._write_comment(number, key, body, held)

    def _held_comments(self, number: int) -> dict[str, tuple[int, str]] | None:
        """What this projection has already said on one issue, keyed by moment —
        or None when GitHub would not say, in which case nothing is written: a
        comment posted blind is a comment posted twice."""
        result = self.call(
            "api", f"repos/{self.repo}/issues/{number}/comments?per_page=100", action="comment"
        )
        return projected_comments(result.json()) if result.ok else None

    def _write_comment(
        self, number: int, key: str, body: str, held: dict[str, tuple[int, str]]
    ) -> None:
        """One already-marked comment, posted once and converged ever after."""
        current = held.get(key)
        if current is None:
            self.call(
                "api", f"repos/{self.repo}/issues/{number}/comments",
                "-X", "POST",
                "-f", f"body={body}",
                action="comment",
            )
        elif current[1] != body:
            self.call(
                "api", f"repos/{self.repo}/issues/comments/{current[0]}",
                "-X", "PATCH",
                "-f", f"body={body}",
                action="comment",
            )

    # -- the two moments a human must feel ------------------------------------

    def escalate(
        self,
        spec: Spec,
        issue: Issue | None = None,
        reason: str = "",
        lines: list[str] | None = None,
    ) -> None:
        """needs-human, delivered: the durable reason as a comment where the
        thing stopped — the sub-issue for an Issue, the parent for a Spec — and
        the configured human assigned, so a notification actually fires.

        The escalation *label* is not applied here: state is state, and the
        ordinary convergence of the surface puts ``giro:needs-human`` on
        whatever the store now says is escalated — which is also what clears it
        again when the answer arrives.
        """
        if not self.enabled:
            return
        target = issue.ref if issue is not None else spec.slug
        number = issue.github_issue if issue is not None else spec.github_issue
        if not number:
            return  # nothing projected to escalate on; markdown still has it all
        body = escalation_comment(target, reason, lines, self.settings.assignee)
        self._moment(number, self._key(target, "escalation"), body)
        self.assign(number)

    def resume(self, spec: Spec, issue: Issue | None = None) -> None:
        """The answer, acknowledged where the escalation was raised. The label
        clears itself when the surface next converges on the store's state; this
        is the sentence that says why it did."""
        if not self.enabled:
            return
        target = issue.ref if issue is not None else spec.slug
        number = issue.github_issue if issue is not None else spec.github_issue
        if not number:
            return
        self._moment(number, self._key(target, "resumed"), resumed_comment(target))

    def assign(self, number: int) -> None:
        """The configured human, put on an issue — compared against what GitHub
        holds first, like every other write here, so an escalation answered and
        raised again assigns them once.

        A pure function of the state that is escalated, which is why Reconcile
        can restore it too: an escalation that reached nobody because the
        network was down still has a human to reach.
        """
        who = self.settings.assignee
        if not who or not self.enabled:
            return
        result = self.call(
            "issue", "view", str(number),
            "--repo", self.repo,
            "--json", "assignees",
            action="escalation",
        )
        if not result.ok:
            return
        viewed = result.json()
        rows = viewed.get("assignees", []) if isinstance(viewed, dict) else []
        current = {
            str(row.get("login", "")).lower()
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict)
        }
        if who.lower() in current:
            return
        self.call(
            "issue", "edit", str(number),
            "--repo", self.repo,
            "--add-assignee", who,
            action="escalation",
        )

    def complete(self, spec: Spec, issues: list[Issue]) -> None:
        """Validate green: the draft flipped to ready for review, and the last
        word on the parent. Nothing merges — the merge is the human's, and now
        it is one click on GitHub's own review page (ADR-0005)."""
        if not self.enabled:
            return
        ready = self._ready_for_review(spec.github_pr)
        if spec.github_issue:
            body = completion_comment(spec, issues, spec.github_pr, ready)
            self._moment(spec.github_issue, f"{spec.slug}:complete", body)

    def _ready_for_review(self, pr: int) -> bool:
        """Out of draft, once: what GitHub says the pull request is decides
        whether the flip is made at all, so projecting twice changes nothing —
        including for a human who put it back into draft on purpose. Returns
        whether the pull request is (now) ready for review, so a flip that failed
        soft on a degraded network is never reported to the human as done."""
        if not pr:
            return False
        result = self.call(
            "pr", "view", str(pr), "--repo", self.repo, "--json", "isDraft", action="pr-ready"
        )
        if not result.ok:
            return False
        viewed = result.json()
        if not isinstance(viewed, dict):
            return False
        if not viewed.get("isDraft"):
            return True  # already out of draft — nothing to flip, and it is ready
        if self.call("pr", "ready", str(pr), "--repo", self.repo, action="pr-ready").ok:
            self.note("pr-ready", ok=True, detail=f"{self.repo}#{pr} is ready for review")
            return True
        return False

    # -- preflight ------------------------------------------------------------

    def start(self) -> None:
        """Prove the repository is usable before a Run leans on it.

        Fatal when [github_projection] enabled = true: the operator asked for
        the surface, so a missing binary, an invalid token, or an unreachable
        repository refuses the Run rather than running it silently blind. Only
        runtime GitHub failures (rate limits, transient errors mid-Run) still
        fail soft (ADR-0014) — the loop's outcome never depends on a tracker.
        """
        if not self.enabled:
            return
        report = self.preflight()
        for warning in report.warnings:
            print(f"warning: GitHub Projection — {warning}", file=sys.stderr)
        if report.ready:
            return
        self.ledger.event(
            "projection", action="preflight", ok=False, detail=report.failure
        )
        raise ProjectionError(
            f"GitHub Projection preflight failed: {report.failure} — "
            "set [github_projection] enabled = false to run without projection, "
            "or fix the failure and try again"
        )

    def check(self) -> PreflightReport:
        """Preflight on demand, whatever the configuration says: ``giro project
        --check`` answers "would a Run project onto this repository?", so it
        runs the checks even when Projection is off and reports that apart."""
        forced = replace(self, settings=replace(self.settings, enabled=True))
        return replace(forced.preflight(), enabled=self.settings.enabled)

    def preflight(self) -> PreflightReport:
        """The binary, the auth, the repository, the label set, the identity —
        in that order, stopping at the first failure: a missing ``gh`` makes the
        rest noise, and identity comes last because it can only warn."""
        report = PreflightReport(
            repo=self.repo, token_source=self.token_source, enabled=self.settings.enabled
        )
        steps = (
            ("gh", self._check_binary),
            ("auth", self._check_auth),
            ("repo", self._check_repo),
            ("labels", self._ensure_labels),
            ("identity", self._check_identity),
        )
        stopped = False
        for name, step in steps:
            if stopped:
                report.checks.append(Check(name, "skipped", "not reached"))
                continue
            ok, detail = step()
            report.checks.append(Check(name, "ok" if ok else "failed", detail))
            stopped = not ok
        report.warnings = self._notification_warnings()
        return report

    def _check_binary(self) -> tuple[bool, str]:
        result = self.call("--version", action="preflight")
        if not result.ok:
            return False, result.error
        version = result.stdout.strip().splitlines()
        return True, version[0] if version else "present"

    def _check_auth(self) -> tuple[bool, str]:
        result = self.call("auth", "status", action="preflight")
        if not result.ok:
            return False, (
                f"{result.error} — put {TOKEN_VAR} in {ENV_FILE} or run `gh auth login`"
            )
        return True, f"token from {self.token_source}"

    def _check_identity(self) -> tuple[bool, str]:
        """Who the token is, and who it would notify. Never a failure: an
        identity that cannot notify still projects perfectly well, so this check
        only ever tells."""
        result = self.call("api", "user", "--jq", ".login", action="preflight")
        self.login = result.stdout.strip() if result.ok else ""
        who = f"@{self.login}" if self.login else "unknown — `gh api user` did not say"
        return True, who if self.settings.assignee else f"{who}; {NO_ASSIGNEE}"

    def _notification_warnings(self) -> list[str]:
        """What will render but never ring, and was surely not meant to: the one
        piece of small print a green preflight can still hide, said out loud at
        every Run (the recommendation itself is in docs/design.md)."""
        assignee = self.settings.assignee
        if assignee and self.login and self.login.lower() == assignee.lower():
            return [SELF_NOTIFICATION.format(login=self.login)]
        return []

    def _check_repo(self) -> tuple[bool, str]:
        if not self.repo:
            return False, (
                "no repository — set repo in [github_projection] of giro.toml, or add "
                "a GitHub origin remote for it to be autodetected from"
            )
        result = self.call("repo", "view", self.repo, "--json", "nameWithOwner", action="preflight")
        return (True, "reachable") if result.ok else (False, result.error)

    def _ensure_labels(self) -> tuple[bool, str]:
        """The ``giro:`` label set, created where it is missing — idempotent, so
        a Run that has projected before makes no write at all."""
        listed = self.call(
            "label", "list", "--repo", self.repo, "--json", "name", "--limit", "200",
            action="preflight",
        )
        if not listed.ok:
            return False, listed.error
        rows = listed.json()
        present = {
            row["name"]
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and "name" in row
        }
        created = 0
        for name, color, description in LABELS:
            if name in present:
                continue
            result = self.call(
                "label", "create", name,
                "--repo", self.repo,
                "--color", color,
                "--description", description,
                action="preflight",
            )
            if not result.ok:
                return False, f"could not create label {name}: {result.error}"
            created += 1
        return True, f"{len(LABELS) - created} present, {created} created"


def build_projection(
    cfg: Config, workspace: Workspace, ledger: Ledger | None = None
) -> Projection:
    """The Run's Projection, wired from config.

    Built from the invoking checkout: its origin remote names the repository
    when config does not, and its git-ignored env file holds the token.
    """
    settings = cfg.projection
    token, source = load_token(workspace.root)
    return Projection(
        settings=settings,
        gh=SubprocessGh(timeout=settings.timeout, token=token, cwd=workspace.root),
        repo=settings.repo or parse_repo(workspace.remote_url()),
        token_source=source,
        ledger=ledger or Ledger(),
    )
