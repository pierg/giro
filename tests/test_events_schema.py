"""The events stream is a versioned public contract, documented in
``docs/design.md``'s Events subsection. Adding an event type or a field without
documenting it fails this test: the doc is the source of truth for a consumer
(the GitHub projection, a chat skill, a CI job) that switches on the stream.

A scripted end-to-end fixture Run — happy path: plan → wave → attempt(green) →
validate(pass) → done — walks the emitter, and every event type and field name
that appears is asserted against the Events subsection.
"""

from __future__ import annotations

import re
from pathlib import Path

from giro.drivers import FakeDriver
from giro.ledger import SCHEMA_VERSION, read_events
from giro.store import Store
from giro.workspace import Workspace
from tests.conftest import git, good_worker, make_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN = REPO_ROOT / "docs" / "design.md"

# The parseable contract line the docs use, one per event type. Anything else
# in the subsection is prose the test does not read.
EVENT_LINE = re.compile(r"^- type\s+([A-Za-z0-9_-]+):\s*fields\s+(.+?)\s*$")


def _documented_events() -> dict[str, set[str]]:
    """Parse the Events subsection into ``{type: {field, ...}}``."""
    text = DESIGN.read_text(encoding="utf-8")
    # Confine the parse to the Events subsection so a stray "- type X" elsewhere
    # in design.md can't accidentally document an event.
    start = text.index("### Events")
    end = text.index("\n## ", start)
    subsection = text[start:end]
    contract: dict[str, set[str]] = {}
    for line in subsection.splitlines():
        match = EVENT_LINE.match(line.strip())
        if match:
            name = match.group(1)
            fields = {f.strip() for f in match.group(2).split(",") if f.strip()}
            contract[name] = fields
    return contract


def test_events_subsection_names_every_event_and_field_a_happy_path_emits(project):
    """A happy-path Run — plan → wave → attempt(green) → validate(pass) → done —
    emits every event, and every field name it carries is documented."""
    # Unplan the fixture Spec so the engine's own planner fills the Issues; that
    # is the only way to see a `plan` event in a fresh Run.
    Store(project).load_issues("demo")[0].path.unlink()
    git(project, "add", "-A")
    git(project, "commit", "-m", "let giro plan it")

    planner = FakeDriver(
        [{"issues": [{"title": "Write feature", "body": "Create feature.txt containing ok."}]}]
    )
    judge = FakeDriver([{"verdict": "pass"}])
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        judge=judge,
        planner=planner,
    )
    report = engine.implement("demo")
    assert report.outcome == "all-done"

    runtime = Workspace(project).runtime_path()
    run_dir = next((runtime / "runs").iterdir())
    events = read_events(run_dir)
    assert events, "the Run should have written its story"

    # Every event carries the versioned frame.
    for event in events:
        assert event["schema_version"] == SCHEMA_VERSION
        assert isinstance(event.get("seq"), int)
        assert event.get("at") and event.get("type")
    # And seq is monotonically increasing from 1.
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    contract = _documented_events()
    assert contract, "docs/design.md must have an Events subsection with `- type X: fields …`"

    observed_types = {e["type"] for e in events}
    # The happy path names every stage of the loop: without one of these the
    # fixture is not exercising what the doc-drift test is meant to guard.
    for expected in ("activation", "plan", "wave", "claim", "attempt", "exit"):
        assert expected in observed_types, f"happy path did not emit {expected!r}"

    # Every observed type is documented, and every field it carries is named.
    for event in events:
        kind = event["type"]
        assert kind in contract, (
            f"event type {kind!r} is not documented in docs/design.md — add "
            f"`- type {kind}: fields …` under the Events subsection"
        )
        undocumented = set(event.keys()) - contract[kind]
        assert not undocumented, (
            f"event {kind!r} carries undocumented field(s) {sorted(undocumented)} — "
            f"add them to the `- type {kind}: fields …` line in docs/design.md"
        )


def test_events_subsection_documents_every_known_event_type():
    """A type the engine ships in ``EVENT_TYPES`` but the docs don't name would
    silently pass the happy-path test until a Run exercised it. Guard the whole
    closed set up front."""
    from giro.ledger import EVENT_TYPES

    contract = _documented_events()
    missing = set(EVENT_TYPES) - set(contract)
    assert not missing, (
        f"EVENT_TYPES {sorted(missing)} are not documented under the "
        "Events subsection in docs/design.md"
    )
