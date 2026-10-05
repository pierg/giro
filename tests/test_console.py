"""The dispatch console: answering an escalation from anywhere, and the copy
that teaches the flow.

The engine's side of the console is one behaviour — a human's answer, written
into the Spec's worktree because that is where the Issue's truth lives, is
carried into the Run that answers it rather than refused as a dirty tree. The
rest is prose: the `giro` skill and the published docs have to describe the
shape the engine actually has, or the console is only true in the code.
"""

from pathlib import Path

from giro.drivers import FakeDriver
from giro.workspace import Workspace
from tests.conftest import good_worker, make_engine, spec_store, spec_worktree

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = (REPO_ROOT / "skills" / "giro" / "SKILL.md").read_text(encoding="utf-8")
# The long-form user docs: the README stays short and points here.
README = (REPO_ROOT / "docs" / "guide.md").read_text(encoding="utf-8")
DESIGN = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

ISSUE_REL = "docs/specs/demo/issues/01-write-feature.md"


def test_escalation_answered_in_the_worktree_is_carried_into_the_next_run(project):
    """The console's whole point: the human never leaves their branch. They read
    the escalation, answer it by editing the Issue body where the Issue lives —
    the Spec's worktree — and re-dispatch. That answer is the engine's to commit;
    an uncommitted answer must not read as a dirty tree and refuse the Run."""
    escalating = FakeDriver([{"outcome": "needs-human", "summary": "should deletes cascade?"}])
    assert make_engine(project, worker=escalating).implement(
        "demo/01-write-feature"
    ).outcome == "needs-human"

    issue_path = spec_worktree(project) / ISSUE_REL
    issue_path.write_text(
        issue_path.read_text(encoding="utf-8") + "\nDeletes cascade.\n", encoding="utf-8"
    )

    report = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    ).implement("demo/01-write-feature")

    assert report.outcome == "done"
    assert spec_store(project).load_issues("demo")[0].state == "done"
    # the answer is on the branch, committed by the engine, not left as a dirty
    # tree the next Run would trip over
    on_branch = Workspace(project).read_blob("giro/demo", ISSUE_REL)
    assert on_branch is not None and "Deletes cascade." in on_branch
    Workspace(spec_worktree(project)).ensure_clean()


# -- the skill: a console, not a blocking call --------------------------------


def test_skill_dispatches_detached_and_hands_the_conversation_back():
    assert "giro implement <target> --detach" in SKILL
    assert "giro logs" in SKILL and "giro runs" in SKILL
    # the Run identifier and its branch are what the human is given back
    assert "Run id" in SKILL
    assert "giro/<slug>" in SKILL
    assert "never block" in SKILL.lower() or "do not block" in SKILL.lower()


def test_skill_answers_progress_from_status_and_the_ledger():
    assert "giro status --json" in SKILL
    assert "Ledger" in SKILL
    # progress is read, never re-run
    assert "never re-run" in SKILL.lower() or "never by re-running" in SKILL.lower()


def test_skill_walks_the_escalation_without_leaving_the_human_s_branch():
    assert "re-invoking is the answer" in SKILL
    assert ".giro/worktrees/" in SKILL
    assert "worktree" in SKILL


def test_skill_keeps_its_hard_rules():
    rules = SKILL[SKILL.index("## Hard rules") :]
    assert "frontmatter" in rules and "log" in rules
    assert "implement" in rules  # never implement in this chat context
    assert "merge" in rules


def test_skill_names_only_real_commands():
    """Every `giro <verb>` and flag the skill teaches has to exist in the CLI —
    copy that drifts past the parser is a console that tells the human to type
    errors."""
    import re

    source = (REPO_ROOT / "src" / "giro" / "cli.py").read_text(encoding="utf-8")
    known = set(re.findall(r'add_parser\(\s*"([a-z]+)"', source))
    assert {"implement", "status", "runs", "logs"} <= known  # the console's own verbs

    quoted = re.findall(r"`([^`\n]+)`", SKILL)
    fenced = [line for block in re.findall(r"```[a-z]*\n(.*?)```", SKILL, re.S)
              for line in block.splitlines()]
    verbs = {m.group(1) for s in quoted + fenced if (m := re.match(r"giro ([a-z]+)", s))}
    assert verbs and verbs <= known, verbs - known

    flags = {f for s in quoted + fenced for f in re.findall(r"--[a-z-]+", s)}
    assert flags <= set(re.findall(r'"(--[a-z-]+)"', source)), flags


# -- the published docs -------------------------------------------------------


def test_readme_teaches_dispatch_the_ledger_and_the_worktree():
    assert "--detach" in README
    assert "Ledger" in README
    assert ".giro/worktrees/" in README
    assert "base branch" in README.lower()
    assert "hermetic" in README.lower()
    # the console flow, not a blocking run
    assert "giro status" in README and "giro logs" in README


def test_readme_has_no_checkout_resident_prose():
    stale = [
        "the giro skill runs the loop, streams progress",
        "switches your checkout",
    ]
    for phrase in stale:
        assert phrase not in README, phrase
    assert "never touches your checkout" in README.lower() or (
        "your checkout is never" in README.lower()
    )


def test_design_doc_carries_run_dispatch_and_ledger_in_its_vocabulary():
    vocab = DESIGN[DESIGN.index("## Vocabulary") : DESIGN.index("## The topology rule")]
    for term in ("**Run**", "**Dispatch**", "**Ledger**", "**Base branch**"):
        assert term in vocab, term
    assert "hermetic" in DESIGN.lower()
    assert ".giro/worktrees/" in DESIGN


# -- U5: `giro logs` no longer follows by default -----------------------------


def test_logs_does_not_follow_by_default_on_a_live_run(project, capsys, monkeypatch):
    """The default is no-follow: `giro logs <id>` on a live Run prints what has
    happened and returns at once — a chat skill or CI job that reads progress
    never blocks. `-f` still tails on demand for a human at a terminal."""
    import time

    from giro import cli as cli_mod
    from giro.ledger import Ledger
    from giro.workspace import Workspace

    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    ledger.event("activation", spec="demo", branch="giro/demo", base_branch="main")
    # The Run is live: run.json says nothing about finishing.

    started = time.monotonic()
    assert cli_mod.main(["-C", str(project), "logs", ledger.id]) == 0
    elapsed = time.monotonic() - started

    assert elapsed < 2.0  # returned promptly rather than tailing forever
    out = capsys.readouterr().out
    assert "activation" in out


# -- U6: friendly error when -C points at nothing ------------------------------


def test_traceback_guard_on_missing_root_directory(capsys):
    from giro import cli as cli_mod

    assert cli_mod.main(["-C", "/definitely/not/a/real/path", "status"]) == 1
    err = capsys.readouterr().err
    assert "no such directory" in err
    assert "Traceback" not in err  # never a raw traceback
