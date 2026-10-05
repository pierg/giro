"""The state store — local markdown beside the code.

Specs live at ``docs/specs/<slug>/SPEC.md``; their Issues at
``docs/specs/<slug>/issues/NN-slug.md``. Frontmatter carries exactly the
fields the loops branch on; everything else is prose for humans. The store
is the durable checkpoint that makes every run resumable.

A Spec is born in the checkout but lives on its branch (ADR-0013): once
``giro/<slug>`` exists, that branch's tip is the truth and the checkout's copy
is at best stale. ``BranchStore`` reads a tip without a worktree, and
``SpecView`` picks the right reader per Spec — status and target resolution
both ask it, so a human standing anywhere sees the real state.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .scaffold import issue_id as _next_issue_id
from .scaffold import slugify
from .states import ISSUE_INITIAL, ISSUE_STATES, SPEC_INITIAL, SPEC_STATES
from .workspace import Workspace

__all__ = [
    "BranchStore",
    "Issue",
    "LOG_FENCE",
    "Spec",
    "SpecView",
    "Store",
    "StoreError",
    "log_sections",
    "parse_issue",
    "parse_spec",
    "slugify",
]

# Where an Issue's conversation-owned body ends and the engine's appended log
# begins — the Zone split, written once and findable ever after, so a reader
# (the Projection) can render the story without guessing which headings the
# engine wrote. Invisible wherever markdown is rendered.
LOG_FENCE = "<!-- giro:log -->"


class StoreError(Exception):
    """The markdown store is missing, malformed, or ambiguous."""


def log_sections(body: str) -> list[str]:
    """The engine-appended log sections of an Issue body, in order.

    Append-only, so a section never moves: its position is its identity.
    """
    _, fence, log = body.partition(LOG_FENCE)
    if not fence:
        return []
    parts = re.split(r"(?m)^(?=## )", log)
    return [section.strip() for section in parts if section.lstrip().startswith("## ")]


@dataclass
class Issue:
    spec_slug: str
    id: str  # e.g. "01-parse-config"
    path: Path
    state: str
    blocked_by: list[str] = field(default_factory=list)
    attempts: int = 0
    title: str = ""
    body: str = ""
    # The projected sub-issue's number (ADR-0014): recorded here so a resume,
    # a clone, or a Reconcile finds it instead of making a second one. 0 = not
    # projected yet.
    github_issue: int = 0
    # The validate gate that filed this Issue, when a gap cycle did — stated
    # here rather than read back out of the body, so the projection can name it
    # without parsing prose.
    gap_gate: str = ""
    # The commit sha this Issue's work forks from — the diff base a judged
    # verify gate is given (F10). Set at first claim; every subsequent claim
    # of the same Issue reads it back so a resume never re-anchors the base
    # to whatever HEAD the second Run happens to inherit.
    verify_base: str = ""

    @property
    def ref(self) -> str:
        return f"{self.spec_slug}/{self.id}"


@dataclass
class Spec:
    slug: str
    path: Path
    state: str
    base: str = ""  # commit sha the giro/<slug> branch grew from
    base_branch: str = ""  # the branch it forked from, resolved once at first activation
    gap_cycles: int = 0
    # The projected surface's identifiers (ADR-0014): recorded here so the
    # mapping survives clones, resumes, and Reconcile. 0 = not projected yet.
    github_issue: int = 0
    github_pr: int = 0
    title: str = ""
    body: str = ""


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, object], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter zone at all: a plain artifact, authored without giro by
        # the `spec`/`plan` skills or by hand. Its whole text is body; the state
        # fields default (see parse_spec/parse_issue), and the engine stamps the
        # real frontmatter when the Spec activates. A *malformed* zone (below)
        # still fails loud — this tolerates absence, never garbage.
        return {}, text
    fields: dict[str, object] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise StoreError(f"{path}: bad frontmatter line {line!r}")
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            fields[key.strip()] = [p.strip() for p in inner.split(",") if p.strip()]
        elif raw.isdigit():
            fields[key.strip()] = int(raw)
        else:
            fields[key.strip()] = raw
    return fields, text[match.end() :]


def _render_frontmatter(fields: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _title_of(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def parse_spec(text: str, slug: str, path: Path) -> Spec:
    """A Spec from its markdown — ``path`` is where it was read, or where the
    reader's copy of it lives."""
    fields, body = _parse_frontmatter(text, path)
    state = str(fields.get("state") or SPEC_INITIAL)
    if state not in SPEC_STATES:
        raise StoreError(
            f"{path}: invalid spec state {state!r} (valid: {', '.join(sorted(SPEC_STATES))})"
        )
    return Spec(
        slug=slug,
        path=path,
        state=state,
        base=str(fields.get("base", "")),
        base_branch=str(fields.get("base_branch", "")),
        gap_cycles=int(fields.get("gap_cycles", 0)),  # type: ignore[arg-type]
        github_issue=int(fields.get("github_issue", 0)),  # type: ignore[arg-type]
        github_pr=int(fields.get("github_pr", 0)),  # type: ignore[arg-type]
        title=_title_of(body),
        body=body,
    )


def parse_issue(text: str, slug: str, issue_id: str, path: Path) -> Issue:
    fields, body = _parse_frontmatter(text, path)
    state = str(fields.get("state") or ISSUE_INITIAL)
    if state not in ISSUE_STATES:
        raise StoreError(
            f"{path}: invalid issue state {state!r} (valid: {', '.join(sorted(ISSUE_STATES))})"
        )
    blocked = fields.get("blocked_by", [])
    if not isinstance(blocked, list):
        raise StoreError(f"{path}: 'blocked_by' must be a list")
    return Issue(
        spec_slug=slug,
        id=issue_id,
        path=path,
        state=state,
        blocked_by=list(blocked),
        attempts=int(fields.get("attempts", 0)),  # type: ignore[arg-type]
        title=_title_of(body),
        body=body,
        github_issue=int(fields.get("github_issue", 0)),  # type: ignore[arg-type]
        gap_gate=str(fields.get("gap_gate", "")),
        verify_base=str(fields.get("verify_base", "")),
    )


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.specs_dir = root / "docs" / "specs"

    # -- specs ---------------------------------------------------------------

    def spec_slugs(self) -> list[str]:
        """Which Specs this tree holds — asked before anything is parsed, so a
        malformed Spec cannot hide the others from a listing."""
        if not self.specs_dir.is_dir():
            return []
        return sorted(path.parent.name for path in self.specs_dir.glob("*/SPEC.md"))

    def list_specs(self) -> list[Spec]:
        return [spec for slug in self.spec_slugs() if (spec := self.load_spec(slug))]

    def load_spec(self, slug: str) -> Spec | None:
        path = self.specs_dir / slug / "SPEC.md"
        if not path.is_file():
            return None
        return parse_spec(path.read_text(encoding="utf-8"), slug, path)

    def save_spec(self, spec: Spec) -> None:
        fields: dict[str, object] = {"state": spec.state}
        if spec.base:
            fields["base"] = spec.base
        if spec.base_branch:
            fields["base_branch"] = spec.base_branch
        if spec.gap_cycles:
            fields["gap_cycles"] = spec.gap_cycles
        if spec.github_issue:
            fields["github_issue"] = spec.github_issue
        if spec.github_pr:
            fields["github_pr"] = spec.github_pr
        spec.path.write_text(_render_frontmatter(fields) + spec.body, encoding="utf-8")

    def create_spec(self, slug: str, body: str) -> Spec:
        spec_dir = self.specs_dir / slug
        if (spec_dir / "SPEC.md").exists():
            raise StoreError(f"spec {slug!r} already exists")
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec = Spec(slug=slug, path=spec_dir / "SPEC.md", state="draft", body=body)
        self.save_spec(spec)
        return spec

    # -- issues --------------------------------------------------------------

    def load_issues(self, slug: str) -> list[Issue]:
        issues_dir = self.specs_dir / slug / "issues"
        if not issues_dir.is_dir():
            return []
        return [
            parse_issue(path.read_text(encoding="utf-8"), slug, path.stem, path)
            for path in sorted(issues_dir.glob("*.md"))
        ]

    def save_issue(self, issue: Issue) -> None:
        fields: dict[str, object] = {
            "state": issue.state,
            "blocked_by": issue.blocked_by,
            "attempts": issue.attempts,
        }
        if issue.gap_gate:
            fields["gap_gate"] = issue.gap_gate
        if issue.github_issue:
            fields["github_issue"] = issue.github_issue
        if issue.verify_base:
            fields["verify_base"] = issue.verify_base
        issue.path.write_text(_render_frontmatter(fields) + issue.body, encoding="utf-8")

    def create_issue(
        self,
        slug: str,
        title: str,
        body: str,
        blocked_by: list[str] | None = None,
        gap_gate: str = "",
    ) -> Issue:
        issues_dir = self.specs_dir / slug / "issues"
        issues_dir.mkdir(parents=True, exist_ok=True)
        issue_id = _next_issue_id(issues_dir, title)
        issue = Issue(
            spec_slug=slug,
            id=issue_id,
            path=issues_dir / f"{issue_id}.md",
            state="ready",
            blocked_by=list(blocked_by or []),
            body=f"# {title}\n\n{body.rstrip()}\n",
            title=title,
            gap_gate=gap_gate,
        )
        self.save_issue(issue)
        return issue

    def append_log(self, issue: Issue, heading: str, lines: list[str]) -> None:
        """Append an attempt-log section to the issue body — the durable story.

        The first section lays the fence that separates that story from the
        conversation's own body, so both a human and the Projection can tell
        which headings the engine wrote.
        """
        block = [f"\n## {heading}\n"]
        block += [f"- {line}" for line in lines]
        body = issue.body.rstrip()
        if LOG_FENCE not in body:
            body += f"\n\n{LOG_FENCE}"
        issue.body = body + "\n" + "\n".join(block) + "\n"
        self.save_issue(issue)

    # -- resolution ----------------------------------------------------------

    def resolve(self, target: str) -> Spec | tuple[Spec, Issue]:
        """Resolve a CLI target: a spec slug, ``slug/issue-id``, or a bare issue id."""
        return _resolve(target, self.load_spec, self.load_issues, self.spec_slugs())


def _resolve(
    target: str,
    load_spec: Callable[[str], Spec | None],
    load_issues: Callable[[str], list[Issue]],
    slugs: list[str],
) -> Spec | tuple[Spec, Issue]:
    """Resolve a target against whatever reader is handed in — one tree, one
    branch tip, or a per-Spec mix of the two."""
    if "/" in target:
        slug, _, issue_id = target.partition("/")
        spec = load_spec(slug)
        if spec is None:
            raise StoreError(f"no spec named {slug!r}")
        issues = load_issues(slug)
        for issue in issues:
            if issue.id == issue_id:
                return spec, issue
        # A bare numeric issue id — "01", "2" — matches the "NN-…" issue whose
        # number prefix is that padded value, when exactly one such issue exists
        # under this Spec. Ambiguity is a loud error, never a guess.
        numeric = _numeric_prefix_matches(issue_id, issues)
        if len(numeric) == 1:
            return spec, numeric[0]
        if len(numeric) > 1:
            ids = ", ".join(i.id for i in numeric)
            raise StoreError(
                f"issue id {issue_id!r} under spec {slug!r} is ambiguous: {ids}"
            )
        raise StoreError(f"no issue {issue_id!r} under spec {slug!r}")

    spec = load_spec(target)
    if spec is not None:
        return spec

    matches: list[tuple[Spec, Issue]] = []
    for slug in slugs:
        candidate = load_spec(slug)
        if candidate is None:
            continue
        for issue in load_issues(slug):
            if issue.id == target:
                matches.append((candidate, issue))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        refs = ", ".join(i.ref for _, i in matches)
        raise StoreError(f"issue id {target!r} is ambiguous: {refs}")
    raise StoreError(f"no spec or issue matches {target!r}")


def _numeric_prefix_matches(issue_id: str, issues: list[Issue]) -> list[Issue]:
    """Every issue whose ``NN-…`` prefix matches ``issue_id`` read as a number.

    A bare number typed by a human — ``01``, ``2`` — is padded to two digits
    and matched against the ``NN-`` prefix on disk, so ``giro implement
    my-feature/01`` resolves to ``my-feature/01-parse-config`` when there is
    exactly one such issue.
    """
    if not issue_id.isdigit():
        return []
    prefix = f"{int(issue_id):02d}-"
    return [i for i in issues if i.id.startswith(prefix)]


class BranchStore:
    """Specs as their branches tell them.

    Every engine commit lands on ``giro/<slug>``, so that tip — not the
    checkout — is where a running Spec's state actually is. This reads it
    straight out of git: no worktree, no checkout switch, nothing on disk. It
    is read-only by construction; the engine writes through its own worktree.

    The paths on what it returns point at the checkout's copy of the same file.
    They are anchors for a human to read, not files these objects were loaded
    from — and never somewhere to save them back to.
    """

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.specs_dir = workspace.root / "docs" / "specs"

    def spec_slugs(self) -> list[str]:
        return self.workspace.spec_branch_slugs()

    def list_specs(self) -> list[Spec]:
        return [spec for slug in self.spec_slugs() if (spec := self.load_spec(slug))]

    def load_spec(self, slug: str) -> Spec | None:
        text = self.workspace.read_blob(f"giro/{slug}", f"docs/specs/{slug}/SPEC.md")
        if text is None:
            return None
        return parse_spec(text, slug, self.specs_dir / slug / "SPEC.md")

    def load_issues(self, slug: str) -> list[Issue]:
        issues_dir = f"docs/specs/{slug}/issues"
        issues = []
        for name in sorted(self.workspace.list_tree(f"giro/{slug}", issues_dir)):
            if not name.endswith(".md"):
                continue
            text = self.workspace.read_blob(f"giro/{slug}", f"{issues_dir}/{name}")
            if text is None:
                continue
            path = self.specs_dir / slug / "issues" / name
            issues.append(parse_issue(text, slug, name[: -len(".md")], path))
        return issues

    def resolve(self, target: str) -> Spec | tuple[Spec, Issue]:
        return _resolve(target, self.load_spec, self.load_issues, self.spec_slugs())


class SpecView:
    """Every Spec read where its truth lives.

    A Spec with a ``giro/<slug>`` branch is read from that branch's tip; a draft
    Spec, which has no branch yet, is read from the invoking checkout. Status
    and target resolution both look through this, so the answer never depends on
    which branch the human happens to be standing on — or on whether that branch
    has the Spec's files at all.
    """

    def __init__(self, store: Store, workspace: Workspace):
        self.store = store
        self.branches = BranchStore(workspace)
        self._branch_slugs: set[str] | None = None
        self._on_branch: dict[str, bool] = {}

    def on_branch(self, slug: str) -> bool:
        """Whether this Spec's truth is on its branch — the branch exists *and*
        carries the Spec. A branch forked by a Run that died before seeding
        carries nothing yet, and an empty branch is not truer than the checkout.
        """
        if slug not in self._on_branch:
            if self._branch_slugs is None:
                self._branch_slugs = set(self.branches.spec_slugs())
            self._on_branch[slug] = (
                slug in self._branch_slugs and self.branches.load_spec(slug) is not None
            )
        return self._on_branch[slug]

    def reader(self, slug: str) -> Store | BranchStore:
        return self.branches if self.on_branch(slug) else self.store

    def spec_slugs(self) -> list[str]:
        return sorted({*self.store.spec_slugs(), *self.branches.spec_slugs()})

    def list_specs(self) -> list[Spec]:
        return [spec for slug in self.spec_slugs() if (spec := self.load_spec(slug))]

    def load_spec(self, slug: str) -> Spec | None:
        return self.reader(slug).load_spec(slug)

    def load_issues(self, slug: str) -> list[Issue]:
        return self.reader(slug).load_issues(slug)

    def resolve(self, target: str) -> Spec | tuple[Spec, Issue]:
        return _resolve(target, self.load_spec, self.load_issues, self.spec_slugs())
