"""The documentation phase stands alone.

The `spec`/`plan`/`grill` skills carry no giro dependency, and the engine
ingests the plain artifacts they produce: it defaults a missing state on read,
stamps the real frontmatter when a Spec activates, and fails closed on anything
malformed. These tests pin all three properties.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from giro.cli import _doctor_specs, main
from giro.drivers import FakeDriver
from giro.scaffold import ISSUE_BODY, SPEC_BODY, adr_filename, issue_id, write_adr
from giro.store import Store, StoreError
from giro.workspace import Workspace
from tests.conftest import git, good_worker, make_engine, spec_store

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTHORING_SKILLS = ("spec", "plan", "grill")

# Authoring skills write documents only. They must not name giro or lean on
# its CLI — implementation is whoever reads the files, by any means.
HARD_GIRO_DEPS = (
    "giro new",
    "giro implement",
    "giro install",
    "giro status",
    "giro verify",
    "giro init",
    "giro.toml",
    "--detach",
)


# -- the skills are giro-independent -----------------------------------------


def test_authoring_skills_are_unprefixed_and_the_old_names_are_gone():
    for name in AUTHORING_SKILLS:
        assert (REPO_ROOT / "skills" / name / "SKILL.md").is_file(), name
        assert not (REPO_ROOT / "skills" / f"giro-{name}").exists(), f"giro-{name} still present"


def test_authoring_skills_have_no_hard_giro_cli_dependency():
    for name in AUTHORING_SKILLS:
        text = (REPO_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        for token in HARD_GIRO_DEPS:
            assert token not in text, f"{name} leaks a hard giro dependency: {token!r}"


def test_authoring_skills_do_not_mention_giro():
    for name in AUTHORING_SKILLS:
        skill_dir = REPO_ROOT / "skills" / name
        for path in skill_dir.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            assert "giro" not in text.lower(), f"{path.relative_to(REPO_ROOT)} mentions giro"


def test_authoring_skills_do_not_carry_an_adaptation_footer():
    for name in AUTHORING_SKILLS:
        text = (REPO_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "Matt Pocock" not in text, name
        assert "Adapted from" not in text, name


# -- the scaffold seam writes plain artifacts --------------------------------


def test_write_adr_is_plain_markdown_and_numbered(tmp_path):
    first = write_adr(tmp_path, "First decision")
    second = write_adr(tmp_path, "Second decision")
    assert first.name == "0001-first-decision.md"
    assert second.name == "0002-second-decision.md"
    body = first.read_text(encoding="utf-8")
    assert body.startswith("# First decision")
    assert not body.lstrip().startswith("---")  # an ADR carries no engine state


def test_scaffold_bodies_carry_no_lifecycle_frontmatter():
    for body in (SPEC_BODY, ISSUE_BODY):
        assert "state:" not in body
        assert "attempts:" not in body


def test_issue_and_adr_ids_number_sequentially(tmp_path):
    issues = tmp_path / "issues"
    assert issue_id(issues, "First slice") == "01-first-slice"
    issues.mkdir()
    (issues / "01-first-slice.md").write_text("x")
    assert issue_id(issues, "Second slice") == "02-second-slice"
    assert adr_filename(tmp_path, "A choice") == "0001-a-choice.md"


# -- tolerant reading: absent frontmatter defaults to the initial state ------


def test_plain_spec_with_no_frontmatter_parses_as_draft(project):
    spec_dir = project / "docs" / "specs" / "handwritten"
    spec_dir.mkdir()
    (spec_dir / "SPEC.md").write_text("# Handwritten\n\nA spec authored with no giro.\n")
    spec = Store(project).load_spec("handwritten")
    assert spec is not None and spec.state == "draft" and spec.title == "Handwritten"


def test_plain_issue_with_blocked_by_but_no_state_reads_ready(project):
    """The `plan` skill writes dependency edges but never a state line."""
    path = project / "docs" / "specs" / "demo" / "issues" / "02-next.md"
    path.write_text("---\nblocked_by: [01-write-feature]\n---\n# Next slice\n\nDo more.\n")
    issue = next(i for i in Store(project).load_issues("demo") if i.id == "02-next")
    assert issue.state == "ready" and issue.blocked_by == ["01-write-feature"]


# -- the fail-closed boundary: `doctor` reads artifacts the engine's way -----


def test_doctor_specs_ok_on_valid_artifacts(project):
    (check,) = _doctor_specs(project)
    assert check["ok"] and "parse" in check["detail"]


def test_doctor_specs_none_yet_is_ready_to_author(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    (check,) = _doctor_specs(root)
    assert check["ok"] and "none yet" in check["detail"]


def test_doctor_specs_flags_a_dangling_edge(project):
    store = Store(project)
    issue = store.load_issues("demo")[0]
    issue.blocked_by = ["99-nope"]
    store.save_issue(issue)
    (check,) = _doctor_specs(project)
    assert not check["ok"] and "99-nope" in check["detail"]


def test_doctor_specs_flags_an_invalid_state(project):
    bad = project / "docs" / "specs" / "demo" / "issues" / "02-bad.md"
    bad.write_text("---\nstate: whoops\n---\n# Bad\n")
    (check,) = _doctor_specs(project)
    assert not check["ok"] and "whoops" in check["detail"]


def test_doctor_specs_flags_a_blocking_cycle(project):
    assert main(["-C", str(project), "new", "issue", "demo", "Second"]) == 0
    store = Store(project)
    a, b = store.load_issues("demo")
    a.blocked_by = [b.id]
    b.blocked_by = [a.id]
    store.save_issue(a)
    store.save_issue(b)
    (check,) = _doctor_specs(project)
    assert not check["ok"] and "cycle" in check["detail"]


# -- the whole vision, end to end --------------------------------------------


def test_engine_ingests_and_stamps_a_hand_authored_plain_spec(project):
    """A Spec and Issue authored with no giro — plain markdown, no frontmatter —
    read back at their initial states, and the engine stamps the real state as it
    runs them to done."""
    spec = project / "docs" / "specs" / "demo" / "SPEC.md"
    spec.write_text("# Demo feature\n\nThe repo contains feature.txt saying ok.\n")
    issue = project / "docs" / "specs" / "demo" / "issues" / "01-write-feature.md"
    issue.write_text("# Write feature file\n\nCreate feature.txt containing ok.\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "plain, hand-authored artifacts")

    store = Store(project)
    assert store.load_spec("demo").state == "draft"
    assert store.load_issues("demo")[0].state == "ready"

    report = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=FakeDriver([{"verdict": "pass"}]),
    ).implement("demo")
    assert report.outcome == "all-done"

    # The engine stamped frontmatter on the branch where the Spec now lives.
    assert spec_store(project).load_spec("demo").state == "done"
    blob = Workspace(project).read_blob("giro/demo", "docs/specs/demo/SPEC.md")
    assert blob is not None and blob.startswith("---\nstate: done")


def test_malformed_artifact_never_reaches_a_run(project):
    """Tolerance is for absence, not garbage: a genuinely malformed Issue still
    fails loud on load, so the engine never trusts it."""
    bad = project / "docs" / "specs" / "demo" / "issues" / "02-bad.md"
    bad.write_text("---\nstate: nonsense\n---\n# Bad\n")
    with pytest.raises(StoreError, match="invalid issue state"):
        Store(project).load_issues("demo")
