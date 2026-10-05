import pytest

from giro.store import Store, StoreError, slugify


def test_roundtrip_and_resolution(project):
    store = Store(project)
    spec = store.load_spec("demo")
    assert spec.state == "draft" and spec.title == "Demo feature"

    issues = store.load_issues("demo")
    assert [i.id for i in issues] == ["01-write-feature"]
    issue = issues[0]
    assert issue.state == "ready" and issue.attempts == 0 and issue.blocked_by == []

    issue.state = "in-progress"
    issue.attempts = 2
    store.save_issue(issue)
    reloaded = store.load_issues("demo")[0]
    assert reloaded.state == "in-progress" and reloaded.attempts == 2
    assert "Create feature.txt" in reloaded.body


def test_resolve_forms(project):
    store = Store(project)
    assert store.resolve("demo").slug == "demo"
    spec, issue = store.resolve("demo/01-write-feature")
    assert issue.id == "01-write-feature"
    spec, issue = store.resolve("01-write-feature")  # bare id, unambiguous
    assert spec.slug == "demo"
    with pytest.raises(StoreError):
        store.resolve("nope")


def test_create_issue_numbering_and_slug(project):
    store = Store(project)
    created = store.create_issue("demo", "Handle the Edge Case!", "Body text.")
    assert created.id == "02-handle-the-edge-case"
    assert created.state == "ready"
    assert store.load_issues("demo")[1].title == "Handle the Edge Case!"


def test_append_log_preserves_body(project):
    store = Store(project)
    issue = store.load_issues("demo")[0]
    store.append_log(issue, "Attempt 1 — gates failed", ["test: exited 1"])
    reloaded = store.load_issues("demo")[0]
    assert "## Attempt 1 — gates failed" in reloaded.body
    assert "Create feature.txt" in reloaded.body


def test_slugify():
    assert slugify("Fix: the (weird) thing") == "fix-the-weird-thing"


def test_resolve_bare_number_when_unambiguous(project):
    store = Store(project)
    spec, issue = store.resolve("demo/01")
    assert issue.id == "01-write-feature" and spec.slug == "demo"
    # unpadded numbers work too
    spec, issue = store.resolve("demo/1")
    assert issue.id == "01-write-feature"


def test_resolve_bare_number_ambiguous_errors_naming_matches(project):
    """Two "01-…" issues under one Spec should not resolve — the error names
    both, so the human can retype the full id."""
    (project / "docs" / "specs" / "demo" / "issues" / "01-other.md").write_text(
        "---\nstate: ready\nblocked_by: []\nattempts: 0\n---\n# Other\n\nBody.\n"
    )
    with pytest.raises(StoreError, match="ambiguous"):
        Store(project).resolve("demo/01")


def test_resolve_bare_number_no_match_errors_cleanly(project):
    with pytest.raises(StoreError, match="no issue '99'"):
        Store(project).resolve("demo/99")
