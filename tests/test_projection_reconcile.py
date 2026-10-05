"""Reconcile: GitHub reconverged on the markdown store.

The other Projection suites script the `gh` boundary and assert the calls; a
convergence cannot be judged that way, because what a second Reconcile does
depends on what the first one left behind. So this one plays a GitHub that
remembers — issues with labels, open/closed states, bodies, assignees and
comments; sub-issue links; one pull request and its draft flag — and asserts
the *surface*: what a human would see. Drift is injected into it by hand, and
the store stays where it was, byte for byte, because Reconcile runs one way.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from giro import cli
from giro.config import load_config
from giro.drivers import FakeDriver
from giro.projection import (
    BODY_MARKER,
    COMMENT_MARKER,
    DISCOVER_LIMIT,
    ZONE_END,
    ZONE_START,
    FakeGh,
    GhResult,
    ProjectionError,
    issue_body,
    render_zone,
)
from giro.reconcile import build_reconciler
from giro.store import Store, log_sections
from giro.workspace import Workspace
from tests.conftest import MINIMAL_TOML, git, good_worker, make_engine, spec_store
from tests.test_projection import LABEL_NAMES, PROJECTION_TOML, projection_for
from tests.test_projection_issues import field, flag
from tests.test_projection_spec import escalating_worker, passing_judge, spy_git

REPO = "acme/widgets"
ISSUES = f"repos/{REPO}/issues"

# A generated comment keyed to one Run — its plan, its waves, its Validate
# verdicts, its escalations. A Run's own story, which the store cannot tell and
# Reconcile therefore never re-tells; everything else here is the store's.
RUN_KEYED = re.compile(r"^[^:]+:[^:]+:[^:]+$")

READ_ONLY_GIT = frozenset(
    {"rev-parse", "show", "ls-tree", "for-each-ref", "ls-remote", "remote", "branch", "status"}
)


class FakeHub(FakeGh):
    """The smallest GitHub a convergence can be judged against.

    Scripted answers still win — a test injects a failure by naming the command
    — and everything else is served from state that the calls actually change,
    so drift can be pushed in by hand and idempotency is exercised rather than
    assumed.
    """

    def __init__(self, results=None, labels=LABEL_NAMES):
        super().__init__(results)
        self.labels: set[str] = set(labels)
        self.issues: dict[int, dict] = {}
        self.prs: dict[int, dict] = {}
        self.statuses: list[tuple[str, str]] = []
        self.next = 1
        self.next_comment = 500

    # -- what a test asks it ------------------------------------------------

    def crowd(self, count: int) -> None:
        """Issues that are nobody's but a human's — a repository with a life of
        its own, busier than one scan window can hold."""
        for n in range(count):
            self._new_issue(f"unrelated {n}", "someone else's issue", [])

    def issue_marked(self, marker: str) -> dict:
        """One giro-generated issue by its hidden marker — how a test finds a
        thing whose number it never learned."""
        return next(
            row
            for row in self.issues.values()
            if (found := BODY_MARKER.search(row["body"])) and found.group(1) == marker
        )

    # -- the boundary -------------------------------------------------------

    def run(self, args: list[str]) -> GhResult:
        joined = " ".join(args)
        if any(joined.startswith(key) for key in self.results):
            return super().run(args)
        self.calls.append(list(args))
        body = self._answer(args)
        return GhResult(0, body if isinstance(body, str) else json.dumps(body), args=list(args))

    def _mint(self) -> int:
        number, self.next = self.next, self.next + 1
        return number

    def _new_issue(self, title: str, body: str, labels: list[str]) -> dict:
        number = self._mint()
        row = {
            "number": number,
            "id": 9000 + number,
            "title": title,
            "body": body,
            "labels": set(labels),
            "state": "OPEN",
            "reason": "",
            "assignees": [],
            "comments": [],
            "children": [],
            "blocked_by": [],
        }
        self.issues[number] = row
        return row

    def _answer(self, args: list[str]) -> str | object:
        head = args[:2]
        if args[0] == "api":
            return self._api(args)
        if args[0] == "--version":
            return "gh version 2.63.2\n"
        if head == ["auth", "status"]:
            return "Logged in to github.com as giro-bot\n"
        if head == ["repo", "view"]:
            return {"nameWithOwner": REPO}
        if head == ["label", "list"]:
            return [{"name": name} for name in sorted(self.labels)]
        if head == ["label", "create"]:
            self.labels.add(args[2])
            return ""
        if head == ["issue", "create"]:
            labels = [name for name in flag(args, "--label").split(",") if name]
            row = self._new_issue(flag(args, "--title"), flag(args, "--body"), labels)
            return f"https://github.com/{REPO}/issues/{row['number']}\n"
        if head == ["issue", "list"]:
            # newest first and cut to the limit, as `gh issue list` answers
            wanted = {name for name in flag(args, "--label").split(",") if name}
            rows = [
                {"number": n, "body": row["body"]}
                for n, row in sorted(self.issues.items(), reverse=True)
                if wanted <= row["labels"]
            ]
            return rows[: int(flag(args, "--limit") or len(rows))]
        if head == ["issue", "view"]:
            return self._view(self.issues[int(args[2])], flag(args, "--json"))
        if head == ["issue", "edit"]:
            return self._edit(self.issues[int(args[2])], args)
        if head == ["issue", "close"]:
            row = self.issues[int(args[2])]
            row["state"], row["reason"] = "CLOSED", flag(args, "--reason").upper().replace(" ", "_")
            return ""
        if head == ["issue", "reopen"]:
            row = self.issues[int(args[2])]
            row["state"], row["reason"] = "OPEN", ""
            return ""
        if head == ["pr", "create"]:
            number = self._mint()
            self.prs[number] = {
                "number": number,
                "head": flag(args, "--head"),
                "base": flag(args, "--base"),
                "title": flag(args, "--title"),
                "body": flag(args, "--body"),
                "isDraft": "--draft" in args,
            }
            return f"https://github.com/{REPO}/pull/{number}\n"
        if head == ["pr", "list"]:
            wanted = flag(args, "--head")
            return [{"number": n} for n, pr in self.prs.items() if pr["head"] == wanted]
        if head == ["pr", "view"]:
            return self._view(self.prs[int(args[2])], flag(args, "--json"))
        if head == ["pr", "edit"]:
            self.prs[int(args[2])]["body"] = flag(args, "--body")
            return ""
        if head == ["pr", "ready"]:
            self.prs[int(args[2])]["isDraft"] = False
            return ""
        return ""

    def _view(self, row: dict, fields: str) -> dict:
        answer: dict[str, object] = {}
        for name in [f for f in fields.split(",") if f]:
            if name == "labels":
                answer["labels"] = [{"name": n} for n in sorted(row["labels"])]
            elif name == "stateReason":
                answer["stateReason"] = row["reason"]
            elif name == "assignees":
                answer["assignees"] = [{"login": who} for who in row["assignees"]]
            else:
                answer[name] = row[name]
        return answer

    def _edit(self, row: dict, args: list[str]) -> str:
        row["labels"] |= {n for n in flag(args, "--add-label").split(",") if n}
        row["labels"] -= {n for n in flag(args, "--remove-label").split(",") if n}
        if "--body" in args:
            row["body"] = flag(args, "--body")
        if who := flag(args, "--add-assignee"):
            row["assignees"].append(who)
        return ""

    def _api(self, args: list[str]) -> str | object:
        path = args[1].partition("?")[0]
        post = args[args.index("-X") + 1 :][:1] == ["POST"] if "-X" in args else False
        patch = args[args.index("-X") + 1 :][:1] == ["PATCH"] if "-X" in args else False
        if path == "user":
            return "giro-bot\n"
        if path == ISSUES and post:
            labels = [a[len("labels[]=") :] for a in args if a.startswith("labels[]=")]
            row = self._new_issue(field(args, "title"), field(args, "body"), labels)
            return self._view(row, "number,id,state,labels,body")
        if found := re.fullmatch(rf"{ISSUES}/(\d+)", path):
            return f"{self.issues[int(found.group(1))]['id']}\n"
        if found := re.fullmatch(rf"{ISSUES}/(\d+)/sub_issues", path):
            row = self.issues[int(found.group(1))]
            if not post:
                return [{"number": n} for n in row["children"]]
            row["children"].append(int(field(args, "sub_issue_id")) - 9000)
            return {}
        if found := re.fullmatch(rf"{ISSUES}/(\d+)/dependencies/blocked_by", path):
            row = self.issues[int(found.group(1))]
            if not post:
                return [{"number": n} for n in row["blocked_by"]]
            row["blocked_by"].append(int(field(args, "issue_id")) - 9000)
            return {}
        if found := re.fullmatch(rf"{ISSUES}/(\d+)/comments", path):
            row = self.issues[int(found.group(1))]
            if not post:
                return row["comments"]
            comment = {"id": self.next_comment, "body": field(args, "body")}
            self.next_comment += 1
            row["comments"].append(comment)
            return comment
        if (found := re.fullmatch(rf"{ISSUES}/comments/(\d+)", path)) and patch:
            for row in self.issues.values():
                for comment in row["comments"]:
                    if comment["id"] == int(found.group(1)):
                        comment["body"] = field(args, "body")
            return {}
        if found := re.fullmatch(rf"repos/{REPO}/statuses/(\w+)", path):
            self.statuses.append((found.group(1), field(args, "context")))
            return {}
        return {}


# -- reading a hub the way a human reads GitHub -------------------------------


def numberless(text: str) -> str:
    """Text with issue numbers blanked — two repositories that numbered the
    same surface differently still say the same thing."""
    return re.sub(r"#\d+", "#N", text)


def surface_of(hub: FakeHub) -> dict:
    """Everything on a hub that is a function of the store, keyed by marker.

    Deliberately not everything on it: a Run's own narration and the commit
    statuses it hung on the head it published belong to that Run, not to the
    markdown, and Reconcile does not re-tell them.
    """
    surface: dict[str, object] = {}
    for row in hub.issues.values():
        found = BODY_MARKER.search(row["body"])
        key = found.group(1) if found else f"human:{row['title']}"
        surface[key] = {
            "title": row["title"],
            "labels": sorted(row["labels"]),
            "state": row["state"],
            "reason": row["reason"],
            "body": numberless(row["body"]),
            "assignees": sorted(row["assignees"]),
            "comments": sorted(
                numberless(comment["body"])
                for comment in row["comments"]
                if (mark := COMMENT_MARKER.search(comment["body"]))
                and not RUN_KEYED.match(mark.group(1))
            ),
            "children": len(row["children"]),
        }
    for pr in hub.prs.values():
        surface[f"pull:{pr['head']}"] = {
            "base": pr["base"],
            "isDraft": pr["isDraft"],
            "body": numberless(pr["body"]),
        }
    return surface


# -- the fixtures a Reconcile is asked to converge ----------------------------


def origin_for(root: Path) -> Path:
    """A bare repository as this checkout's origin."""
    remote = root.parent / f"{root.name}-origin.git"
    git(root, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    return remote


def projected_toml(project: Path) -> None:
    (project / "giro.toml").write_text(PROJECTION_TOML)


def reconciler(project: Path, hub: FakeHub, **settings):
    return build_reconciler(
        load_config(project), project, projection_for(hub, **settings)
    )


def reconcile(project: Path, hub: FakeHub, target: str = "", **settings):
    return reconciler(project, hub, **settings).run(target)


def branched(project: Path, spec_state: str, issue_states: tuple[str, ...] = ("ready",)) -> Path:
    """The store a Run leaves at a given lifecycle point, without spending one:
    the Spec on its own branch, in the state named, with a story in its log."""
    workspace, _ = Workspace(project).ensure_spec_worktree("demo", "main")
    store = Store(workspace.root)
    spec = store.load_spec("demo")
    spec.state, spec.base_branch = spec_state, "main"
    store.save_spec(spec)
    for issue, state in zip(store.load_issues("demo"), issue_states, strict=False):
        issue.state = state
        if state != "ready":
            store.append_log(issue, f"Attempt 1 — {state}", ["worker: wrote the thing"])
        store.save_issue(issue)
    workspace.commit_all(f"test: demo is {spec_state}")
    return workspace.root


def twin(project: Path) -> Path:
    """A second checkout of the same repository — same store, same history."""
    other = project.parent / "twin" / "target"
    shutil.copytree(project, other)
    return other


# -- a surface built from nothing ---------------------------------------------


def test_reconcile_builds_the_whole_surface_where_a_run_projected_none(project):
    """AC1: a Run that never touched GitHub, rendered after the fact."""
    origin_for(project)
    engine = make_engine(project, worker=FakeDriver([good_worker]), judge=passing_judge())

    assert engine.implement("demo").outcome == "all-done"  # Projection off throughout
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub(labels=[])
    report = reconcile(project, hub)

    spec = spec_store(project).load_spec("demo")
    issue = spec_store(project).load_issues("demo")[0]
    assert hub.labels == set(LABEL_NAMES)  # the label set, ensured before anything wears one
    parent = hub.issue_marked("giro:spec:demo")
    assert parent["labels"] == {"giro:spec", "giro:done"}
    assert parent["state"] == "OPEN"  # done is a label; the human's merge closes it
    sub = hub.issue_marked(f"giro:issue:demo/{issue.id}")
    assert sub["labels"] == {"giro:issue", "giro:done"}
    assert (sub["state"], sub["reason"]) == ("CLOSED", "COMPLETED")
    assert sub["body"] == issue_body(spec, issue)
    assert parent["children"] == [sub["number"]]  # the native progress bar
    # the Issue's story, verbatim, one comment per appended log section
    posted = [re.sub(r"\n\n<!-- giro:comment:.*", "", c["body"]) for c in sub["comments"]]
    assert posted == log_sections(issue.body) and posted
    pull = next(iter(hub.prs.values()))
    assert (pull["head"], pull["base"]) == ("giro/demo", "main")
    assert pull["isDraft"] is False  # the store says done: Validate green, ready for review
    assert f"Closes #{parent['number']}" in pull["body"]
    assert f"- [x] `{issue.id}`" in pull["body"]

    assert report.ok and [s.slug for s in report.specs] == ["demo"]
    kinds = {c.kind for c in report.specs[0].changes} | {c.kind for c in report.repo_changes}
    assert kinds == {"created", "updated"}


def test_a_run_with_a_broken_projection_reconciles_to_the_surface_a_healthy_one_had(project):
    """AC4: the outage costs the timing of the rendering and nothing else."""
    other = twin(project)
    origin_for(project)
    origin_for(other)
    healthy = FakeHub()
    broken = FakeHub(
        {
            "issue create": GhResult(1, stderr="HTTP 502 (server error)"),
            "pr create": GhResult(1, stderr="HTTP 502 (server error)"),
        }
    )
    for root, hub in ((project, healthy), (other, broken)):
        engine = make_engine(
            root,
            worker=FakeDriver([good_worker]),
            judge=passing_judge(),
            projection=projection_for(hub),
        )
        assert engine.implement("demo").outcome == "all-done"
    assert not broken.issues  # the outage swallowed the whole surface

    rebuilt = FakeHub()
    reconcile(project, healthy)  # a healthy Run is reconciled too: it converges
    reconcile(other, rebuilt)  # ...and the outage's Run is built from the store

    assert surface_of(rebuilt) == surface_of(healthy)
    assert set(surface_of(rebuilt)) == {
        "giro:spec:demo",
        "giro:issue:demo/01-write-feature",
        "pull:giro/demo",
    }
    assert healthy.statuses and not rebuilt.statuses  # a Run's verdicts on a Run's head


# -- drift ---------------------------------------------------------------------


def test_injected_drift_is_restored_and_human_prose_is_left_alone(project):
    """AC2: a hand-closed sub-issue, a deleted label, an edited engine zone."""
    origin_for(project)
    hub = FakeHub()
    engine = make_engine(
        project,
        worker=FakeDriver([escalating_worker]),
        projection=projection_for(hub),
    )
    assert engine.implement("demo").outcome == "needs-human"
    spec = spec_store(project).load_spec("demo")
    issue = spec_store(project).load_issues("demo")[0]
    sub = hub.issue_marked(f"giro:issue:demo/{issue.id}")
    parent = hub.issue_marked("giro:spec:demo")
    pull = next(iter(hub.prs.values()))

    # a hand closes the sub-issue, deletes a label off it, scribbles in the
    # engine's fence, and says something of their own in both places
    sub["state"], sub["reason"] = "CLOSED", "NOT_PLANNED"
    sub["labels"].discard("giro:needs-human")
    hub.labels.discard("giro:needs-human")
    sub["comments"].append({"id": 1, "body": "I think the format should be JSON."})
    prose_before, prose_after = "Reviewer: read the migration first.\n\n", "\n\nSigned, a human.\n"
    zone = pull["body"][pull["body"].index(ZONE_START) : pull["body"].index(ZONE_END)]
    pull["body"] = prose_before + zone.replace("state:", "state: (I edited this)") + (
        ZONE_END + prose_after
    )
    human_comments = list(parent["comments"])

    report = reconcile(project, hub)

    assert (sub["state"], sub["reason"]) == ("OPEN", "")  # the store says the loop is not done
    assert sub["labels"] == {"giro:issue", "giro:needs-human"}
    assert "giro:needs-human" in hub.labels  # the deleted label, made again
    assert {"id": 1, "body": "I think the format should be JSON."} in sub["comments"]
    assert pull["body"].startswith(prose_before) and pull["body"].endswith(prose_after)
    assert "(I edited this)" not in pull["body"]
    assert render_zone(spec, [issue], parent["number"]) in pull["body"]
    assert parent["comments"] == human_comments  # a Run's narration is not re-told
    assert report.ok
    # the report is the first thing read after a recovery: it says what moved
    assert f"issue #{sub['number']} reopened" in [c.what for c in report.specs[0].changes]


def test_a_hand_that_re_drafts_a_done_pull_request_is_flipped_back_and_reported(project):
    """AC2 and AC5 together: the flip out of draft is what `done` in the store
    means, so a hand that undoes it is drift — and a visible write is a
    reported write, or the report says nothing happened while GitHub moved."""
    origin_for(project)
    branched(project, "done", ("done",))
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub()
    twice = reconciler(project, hub)
    twice.run()
    pull = next(iter(hub.prs.values()))
    assert pull["isDraft"] is False
    pull["isDraft"] = True  # a hand puts it back into draft

    report = twice.run()

    assert pull["isDraft"] is False
    changes = report.specs[0].changes
    assert [c.kind for c in changes] == ["updated"]
    assert changes[0].what == f"pull request #{pull['number']} ready for review"
    assert "1 updated" in report.specs[0].lines()[-1]


def test_a_repository_busier_than_the_scan_window_still_finds_its_own_surface(project):
    """The marker scan is bounded, so it is also narrowed: an unfiltered window
    on a busy repository would stop finding giro's own issues and build the
    whole surface a second time, every time."""
    origin_for(project)
    branched(project, "active")
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub()
    reconcile(project, hub)
    made = dict(hub.issues)
    hub.crowd(DISCOVER_LIMIT + 20)  # the repository gets on with its life

    report = reconcile(project, hub)  # no identifier recorded anywhere

    generated = {n for n, row in hub.issues.items() if BODY_MARKER.search(row["body"])}
    assert generated == set(made)  # nothing of giro's was made a second time
    assert len(hub.prs) == 1
    assert report.specs[0].changes == []


def test_reconcile_finds_a_surface_the_store_has_no_identifier_for(project):
    """The hidden marker is the mapping of last resort: a checkout that never
    recorded a number still converges the surface it already has."""
    origin_for(project)
    branched(project, "active")
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub()
    reconcile(project, hub)
    made = dict(hub.issues)
    assert spec_store(project).load_spec("demo").github_issue == 0  # nothing written back

    reconcile(project, hub)  # a second Reconcile, still with no identifier to go on

    assert set(hub.issues) == set(made) and len(hub.prs) == 1
    assert [args for args in hub.calls if args[:2] == ["issue", "list"]]  # markers, not memory


# -- one direction only --------------------------------------------------------


def test_reconcile_twice_changes_nothing(project):
    """AC3: idempotent by construction — the second pass writes nothing."""
    origin_for(project)
    branched(project, "needs-human", ("needs-human",))
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub()
    twice = reconciler(project, hub)  # the same Reconciler, asked again
    first = twice.run()
    before = surface_of(hub)
    hub.calls.clear()

    second = twice.run()

    assert surface_of(hub) == before
    assert second.specs[0].changes == [] and second.repo_changes == []
    assert first.specs[0].counts["created"] > 0
    writes = [args for args in hub.calls if args[1:2] in (["create"], ["edit"], ["close"])]
    assert writes == []
    assert [args for args in hub.calls if "-X" in args] == []


def test_reconcile_never_writes_the_store_the_branches_or_any_local_state(project, monkeypatch):
    """AC3: display, not memory — nothing local moves, in either tree."""
    origin_for(project)
    worktree = branched(project, "active")
    git(project, "push", "origin", "giro/demo")

    def snapshot() -> dict:
        return {
            "refs": git(project, "for-each-ref", "--format=%(refname) %(objectname)"),
            "status": git(project, "status", "--porcelain"),
            "branch-status": git(worktree, "status", "--porcelain"),
            "spec": (worktree / "docs" / "specs" / "demo" / "SPEC.md").read_bytes(),
            "issues": sorted(
                (p.name, p.read_bytes())
                for p in (worktree / "docs" / "specs" / "demo" / "issues").iterdir()
            ),
            "runs": sorted(p.name for p in (project / ".giro").iterdir()),
        }

    before = snapshot()
    calls = spy_git(monkeypatch)

    report = reconcile(project, FakeHub())

    assert report.ok
    assert snapshot() == before
    assert calls  # git was used — to *read* the branch the store lives on
    assert [args for args, _code in calls if args[0] not in READ_ONLY_GIT] == []


# -- every lifecycle point -----------------------------------------------------


@pytest.mark.parametrize(
    "spec_state,issue_state,labels,closed",
    [
        ("active", "in-progress", {"giro:issue", "giro:in-progress"}, False),
        ("needs-human", "needs-human", {"giro:issue", "giro:needs-human"}, False),
        ("done", "done", {"giro:issue", "giro:done"}, True),
        ("active", "wontfix", {"giro:issue", "giro:wontfix"}, True),
    ],
)
def test_reconcile_is_safe_at_every_lifecycle_point(
    project, spec_state, issue_state, labels, closed
):
    """AC5: draft, active, needs-human, done — each converges to its own truth."""
    origin_for(project)
    branched(project, spec_state, (issue_state,))
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub()

    report = reconcile(project, hub, assignee="octocat")

    parent = hub.issue_marked("giro:spec:demo")
    sub = hub.issue_marked("giro:issue:demo/01-write-feature")
    assert sub["labels"] == labels
    assert (sub["state"] == "CLOSED") is closed
    assert parent["labels"] == {"giro:spec"} | ({f"giro:{spec_state}"} & set(LABEL_NAMES))
    assert next(iter(hub.prs.values()))["isDraft"] is (spec_state != "done")
    # needs-human is the one state that must reach a human, however it got there
    assigned = spec_state == "needs-human"
    assert (parent["assignees"] == ["octocat"]) is assigned
    assert (sub["assignees"] == ["octocat"]) is (issue_state == "needs-human")
    assert report.ok


def test_a_spec_that_never_ran_is_skipped_and_so_is_an_unpublished_pull_request(project):
    origin_for(project)
    hub = FakeHub()

    draft = reconcile(project, hub)

    assert not hub.issues and not hub.prs
    assert [c.kind for c in draft.specs[0].changes] == ["skipped"]
    assert "nothing has run" in draft.specs[0].changes[0].what

    branched(project, "active")  # a branch now, but never pushed
    report = reconcile(project, hub)

    assert hub.issues and not hub.prs  # the surface, minus the one thing that needs a head
    skipped = [c for c in report.specs[0].changes if c.kind == "skipped"]
    assert len(skipped) == 1 and "not on the remote" in skipped[0].what


# -- the report, and the command ----------------------------------------------


def test_the_report_says_what_it_created_updated_and_skipped(project, capsys):
    projected_toml(project)
    origin_for(project)
    branched(project, "active")
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub(labels=[])
    report = reconciler(project, hub).run()

    assert [c.what for c in report.repo_changes] == [f"label {n}" for n in LABEL_NAMES]
    entry = report.specs[0]
    assert entry.slug == "demo" and entry.state == "active" and entry.issue and entry.pr
    made = [c.what for c in entry.changes if c.kind == "created"]
    assert any(w.startswith("tracking issue #") for w in made)
    assert any(w.startswith("sub-issue #") for w in made)
    assert any(w.startswith("draft pull request #") for w in made)
    for line in report.lines():
        print(line)
    out = capsys.readouterr().out
    assert "demo [active]" in out and "created" in out
    assert json.loads(json.dumps(report.as_dict()))["ok"] is True


def test_the_project_command_reconciles_and_says_how_it_went(project, monkeypatch, capsys):
    projected_toml(project)
    origin_for(project)
    branched(project, "active")
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub()
    monkeypatch.setattr(cli, "build_projection", lambda cfg, workspace: projection_for(hub))

    assert cli.main(["-C", str(project), "project"]) == 0
    human = capsys.readouterr().out
    assert REPO in human and "demo [active]" in human

    assert cli.main(["-C", str(project), "project", "demo", "--json"]) == 0
    machine = json.loads(capsys.readouterr().out)
    assert machine["ok"] is True and machine["specs"][0]["slug"] == "demo"
    assert machine["specs"][0]["changes"] == []  # the second pass had nothing to do

    assert cli.main(["-C", str(project), "project", "--check"]) == 0  # still the preflight


def test_a_failing_call_is_reported_and_never_raised(project):
    projected_toml(project)
    origin_for(project)
    branched(project, "active")
    hub = FakeHub({"issue create": GhResult(1, stderr="HTTP 403 (Resource not accessible)")})

    report = reconcile(project, hub)

    assert not report.ok
    failed = [c for c in report.specs[0].changes if c.kind == "failed"]
    assert len(failed) == 1 and "403" in failed[0].what
    assert "not ready" not in "\n".join(report.lines())


def test_a_read_that_fails_mid_convergence_is_named_in_the_report_and_exits_nonzero(
    project, monkeypatch, capsys
):
    """A read is how a convergence knows what to write. When one fails the
    surface is left short of the store — silently, unless the report says so:
    Reconcile may not crash, but it must never claim a convergence it did not
    make."""
    projected_toml(project)
    origin_for(project)
    branched(project, "active", ("in-progress",))
    git(project, "push", "origin", "giro/demo")
    hub = FakeHub()
    twice = reconciler(project, hub)
    assert twice.run().ok  # the surface, built whole
    sub = hub.issue_marked("giro:issue:demo/01-write-feature")
    assert [c for c in sub["comments"] if COMMENT_MARKER.search(c["body"])]

    # a hand deletes the Issue's story, and GitHub then goes half-dead: the read
    # that would have found the drift is the one that fails
    sub["comments"].clear()
    hub.results[f"api {ISSUES}/{sub['number']}/comments"] = GhResult(
        1, stderr="HTTP 502 (server error)"
    )
    report = twice.run()

    assert sub["comments"] == []  # the gap is real: the story was not re-told
    assert not report.ok
    failed = [c for c in report.specs[0].changes if c.kind == "failed"]
    assert len(failed) == 1 and "502" in failed[0].what
    assert f"comments on issue #{sub['number']}" in failed[0].what  # named, not counted
    lines = "\n".join(report.lines())
    assert "GitHub now says what the markdown says" not in lines
    assert "partially reconciled" in lines and f"issue #{sub['number']}" in lines

    # and the command carrying it says the same thing, and exits saying it
    monkeypatch.setattr(cli, "build_projection", lambda cfg, workspace: twice.projection)
    assert cli.main(["-C", str(project), "project"]) == 1
    printed = capsys.readouterr().out
    assert "partially reconciled" in printed
    assert "GitHub now says what the markdown says" not in printed


class NoDependenciesHub(FakeHub):
    """A repository whose API has no native issue dependencies: it refuses the
    ``blocked_by`` endpoint, every call, forever."""

    def run(self, args: list[str]) -> GhResult:
        if args[:1] == ["api"] and args[1].endswith("/dependencies/blocked_by"):
            self.calls.append(list(args))
            return GhResult(1, stderr="HTTP 404 (Not Found)", args=list(args))
        return super().run(args)


def test_the_one_read_that_may_refuse_is_a_feature_probe_and_not_a_gap(project):
    """The dependency endpoint is asked *whether GitHub does this at all*, and a
    repository that says no loses nothing: the edge is stated in the sub-issue
    body instead. Reporting that as a failure would make every Reconcile on such
    a repository claim a gap it does not have."""
    origin_for(project)
    worktree = branched(project, "active")
    (worktree / "docs" / "specs" / "demo" / "issues" / "02-polish.md").write_text(
        "---\nstate: ready\nblocked_by: [01-write-feature]\nattempts: 0\n---\n"
        "# Polish\n\nRound it out.\n"
    )
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "test: a blocked Issue")
    git(project, "push", "origin", "giro/demo")
    hub = NoDependenciesHub()

    report = reconcile(project, hub)

    blocked = hub.issue_marked("giro:issue:demo/02-polish")
    assert "Blocked by #" in blocked["body"]  # the edge shows, in prose
    assert report.ok and [c for c in report.specs[0].changes if c.kind == "failed"] == []
    assert "GitHub now says what the markdown says" in report.lines()


class RateLimitedHub(FakeHub):
    """A hub whose first content-creating call meets GitHub's secondary rate
    limit — the refusal a rebuild from nothing is likeliest to meet, since it
    issues its creates back to back."""

    def __init__(self, prefix: str = "issue create", **kwargs):
        super().__init__(**kwargs)
        self.prefix, self.limited = prefix, False

    def run(self, args: list[str]) -> GhResult:
        if not self.limited and " ".join(args).startswith(self.prefix):
            self.limited = True
            self.calls.append(list(args))
            return GhResult(
                1, stderr="HTTP 403: You have exceeded a secondary rate limit", args=list(args)
            )
        return super().run(args)


def test_a_write_the_rate_limit_refused_and_the_retry_landed_is_reported_once(
    project, monkeypatch, capsys
):
    """A retry is one logical write. The pause GitHub asked for is not a failure
    of the convergence, so a Reconcile that fully landed says so — and exits as
    though it had."""
    projected_toml(project)
    origin_for(project)
    branched(project, "active")
    git(project, "push", "origin", "giro/demo")
    monkeypatch.setattr("giro.projection.time.sleep", lambda _seconds: None)
    hub = RateLimitedHub()

    report = reconcile(project, hub)

    creates = [args for args in hub.calls if args[:2] == ["issue", "create"]]
    assert len(creates) == 2  # refused once, asked again, landed
    changes = report.specs[0].changes
    assert [c for c in changes if c.kind == "failed"] == []
    assert [c.what for c in changes if c.what.startswith("tracking issue")] == [
        f"tracking issue #{hub.issue_marked('giro:spec:demo')['number']}"
    ]
    assert report.ok

    # and the command carrying it, on a repository just as rate-limited
    monkeypatch.setattr(
        cli, "build_projection", lambda cfg, workspace: projection_for(RateLimitedHub())
    )
    assert cli.main(["-C", str(project), "project"]) == 0
    assert "GitHub now says what the markdown says" in capsys.readouterr().out


def test_reconcile_refuses_when_projection_is_off(project):
    (project / "giro.toml").write_text(MINIMAL_TOML)
    with pytest.raises(ProjectionError, match="Projection is off"):
        reconcile(project, FakeHub(), enabled=False)


def test_a_repository_that_cannot_be_reached_stops_before_it_converges(project):
    projected_toml(project)
    branched(project, "active")
    hub = FakeHub({"repo view": GhResult(1, stderr="Could not resolve to a Repository")})

    report = reconcile(project, hub)

    assert not (report.ok or report.ready) and "Repository" in report.failure
    assert not hub.issues and report.specs == []
    assert "not ready" in "\n".join(report.lines())


def test_the_command_exits_nonzero_when_the_repository_is_not_ready(project, monkeypatch, capsys):
    projected_toml(project)
    branched(project, "active")
    hub = FakeHub({"repo view": GhResult(1, stderr="Could not resolve to a Repository")})
    monkeypatch.setattr(cli, "build_projection", lambda cfg, workspace: projection_for(hub))

    assert cli.main(["-C", str(project), "project"]) == 1
    assert "not ready" in capsys.readouterr().out


def test_reconcile_needs_no_network_beyond_gh_and_never_pushes(project, monkeypatch):
    """A push is a write to a branch; Reconcile makes none, so an unpublished
    branch simply waits for the Run that publishes it."""
    origin_for(project)
    branched(project, "active")
    calls: list[list[str]] = []
    original_run = subprocess.run
    original_popen = subprocess.Popen

    def watch_run(argv, **kwargs):
        if list(argv[:1]) == ["git"]:
            calls.append(list(argv[1:]))
        return original_run(argv, **kwargs)

    def watch_popen(argv, **kwargs):
        # Workspace._git spawns git via Popen — skip the "-c commit.gpgsign=false"
        # dash-c flag pair to recover the effective argv the test asserts against.
        if list(argv[:1]) == ["git"]:
            args = list(argv[1:])
            if args[:2] == ["-c", "commit.gpgsign=false"]:
                args = args[2:]
            calls.append(args)
        return original_popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", watch_run)
    monkeypatch.setattr(subprocess, "Popen", watch_popen)
    reconcile(project, FakeHub())

    assert calls and not [args for args in calls if args[0] in ("push", "commit", "merge")]
