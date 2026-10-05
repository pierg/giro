"""The grill slice: ADR scaffolding, CONTEXT.md injection, default gate roster."""

from giro.cli import main
from giro.config import INIT_TEMPLATE, load_config
from giro.drivers import FakeDriver
from giro.prompts import resolve_prompt
from tests.conftest import good_worker, make_engine


def test_new_adr_numbering_and_shape(tmp_path, capsys):
    assert main(["-C", str(tmp_path), "new", "adr", "Event-sourced orders"]) == 0
    assert main(["-C", str(tmp_path), "new", "adr", "Postgres for the write model"]) == 0
    adrs = sorted(p.name for p in (tmp_path / "docs" / "adr").glob("*.md"))
    assert adrs == ["0001-event-sourced-orders.md", "0002-postgres-for-the-write-model.md"]
    body = (tmp_path / "docs" / "adr" / adrs[0]).read_text()
    assert body.startswith("# Event-sourced orders")


def test_context_md_rides_into_worker_packets(project):
    (project / "CONTEXT.md").write_text(
        "# Demo\n\n## Language\n\n**Widget**:\nThe thing.\n_Avoid_: gadget\n"
    )
    from tests.conftest import git

    git(project, "add", "-A")
    git(project, "commit", "-m", "add glossary")

    fake = FakeDriver([good_worker])
    engine = make_engine(project, worker=fake)
    engine.implement("demo/01-write-feature")
    prompt = fake.calls[0]["prompt"]
    assert "Project language (CONTEXT.md)" in prompt
    assert "_Avoid_: gadget" in prompt


def test_no_context_md_no_section(project):
    fake = FakeDriver([good_worker])
    engine = make_engine(project, worker=fake)
    engine.implement("demo/01-write-feature")
    assert "Project language" not in fake.calls[0]["prompt"]


def test_init_template_parses_with_default_roster(tmp_path):
    (tmp_path / "giro.toml").write_text(INIT_TEMPLATE)
    cfg = load_config(tmp_path)
    names = [(g.name, g.type) for g in cfg.verify_gates]
    assert ("review", "judge") in names
    # A command test gate ships COMMENTED OUT — a project's real test command
    # comes from the giro-setup skill (or a hand-edit), not from a default that
    # would fail out of the box in most repositories.
    assert not any(name == "test" for name, _ in names)
    # conformance ships commented out — enabled by hand once docs exist
    assert not any(name == "conformance" for name, _ in names)


def test_default_judge_criteria_and_grill_resolve(tmp_path):
    # judge criteria live in prompts/, the standalone grill host skill in skills/
    assert "scope creep" in resolve_prompt(tmp_path, "prompts", "review")
    assert "possible stale ADR" in resolve_prompt(tmp_path, "prompts", "conformance")
    assert "frontier" in resolve_prompt(tmp_path, "skills", "grill")


def test_worker_preamble_forbids_docs_edits(project):
    fake = FakeDriver([good_worker])
    engine = make_engine(project, worker=fake)
    engine.implement("demo/01-write-feature")
    prompt = fake.calls[0]["prompt"]
    assert "Never edit CONTEXT.md or docs/adr/" in prompt
