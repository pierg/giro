"""Prompt resolution, the host-skills-only install, and installed-skill drift.

Both directories in the new shape (ADR-0015) are exercised here: ``prompts/``
holds engine-injected plain markdown, ``skills/`` holds host-invocable
SKILL.md operator skills. ``giro install`` places only the latter.
"""

import json
import os
import shutil

from giro.cli import _print_status, main, status_report
from giro.drivers import FakeDriver
from giro.prompts import bundled_dir, installed_skill_drift, resolve_prompt, skill_digest
from tests.conftest import good_worker, make_engine


def test_bundled_dirs_hold_the_right_files():
    skills = bundled_dir("skills")
    assert skills is not None
    for name in ("giro", "giro-setup", "spec", "plan", "grill"):
        assert (skills / name / "SKILL.md").is_file(), name
    prompts = bundled_dir("prompts")
    assert prompts is not None
    for name in ("worker", "planner", "review", "conformance"):
        assert (prompts / f"{name}.md").is_file(), name
    # engine prompts do not masquerade as host skills
    assert not (skills / "worker").exists()
    assert not (skills / "review").exists()


def test_resolution_order_project_wins_for_prompts(tmp_path):
    (tmp_path / "prompts").mkdir(parents=True)
    (tmp_path / "prompts" / "worker.md").write_text("PROJECT OVERRIDE")
    assert resolve_prompt(tmp_path, "prompts", "worker") == "PROJECT OVERRIDE"


def test_resolve_prompt_returns_body_verbatim_for_prompts(tmp_path):
    """Prompts are plain markdown — no YAML frontmatter to strip; the file's
    bytes are what the engine injects."""
    (tmp_path / "prompts").mkdir(parents=True)
    body = "# Foo\n\nNo frontmatter here at all.\n"
    (tmp_path / "prompts" / "foo.md").write_text(body)
    assert resolve_prompt(tmp_path, "prompts", "foo") == body


def test_resolve_prompt_returns_body_verbatim_for_skills(tmp_path):
    """Skills carry their frontmatter for host discovery — the file's bytes
    are returned untouched, because the engine never resolves a skill for
    injection (a host reads it)."""
    (tmp_path / "skills" / "foo").mkdir(parents=True)
    body = "---\nname: foo\ndescription: bar\n---\n\n# Foo\n\nBody text.\n"
    (tmp_path / "skills" / "foo" / "SKILL.md").write_text(body)
    assert resolve_prompt(tmp_path, "skills", "foo") == body


def test_host_skills_honor_claude_dir_but_engine_prompts_do_not(tmp_path):
    # a host skill placed by `giro install` (or a host) is honored
    (tmp_path / ".claude" / "skills" / "spec").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "spec" / "SKILL.md").write_text("HOST COPY")
    assert resolve_prompt(tmp_path, "skills", "spec") == "HOST COPY"
    # ...but the engine never resolves an internal prompt from host-discovery dirs
    (tmp_path / ".claude" / "skills" / "worker").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "worker" / "SKILL.md").write_text("SNEAKY")
    resolved = resolve_prompt(tmp_path, "prompts", "worker") or ""
    assert "SNEAKY" not in resolved  # falls to bundled prompts/worker.md
    assert "worker discipline" in resolved
    # unknown name in any category -> None
    assert resolve_prompt(tmp_path, "prompts", "does-not-exist") is None
    assert resolve_prompt(tmp_path, "skills", "does-not-exist") is None


def test_worker_packet_carries_bundled_discipline(project):
    """Without any project override, the engine injects the bundled worker prompt."""
    fake = FakeDriver([good_worker])
    engine = make_engine(project, worker=fake)
    engine.implement("demo/01-write-feature")
    assert "Test-first." in fake.calls[0]["prompt"]


def test_install_places_only_host_skills(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert main(["-C", str(root), "install"]) == 0
    installed = root / ".claude" / "skills"
    # host skills are placed for discovery
    assert (installed / "giro" / "SKILL.md").is_file()
    assert (installed / "spec" / "SKILL.md").is_file()
    assert (installed / "giro-setup" / "SKILL.md").is_file()
    # engine prompts are NOT surfaced to the host
    assert not (installed / "worker").exists()
    assert not (installed / "review").exists()
    assert not (installed / "conformance").exists()
    assert not (installed / "planner").exists()
    agents = root / ".agents" / "skills"
    assert agents.is_symlink()
    assert os.readlink(agents) == "../.claude/skills"
    assert (agents / "giro" / "SKILL.md").resolve() == (installed / "giro" / "SKILL.md").resolve()


def test_install_skips_then_forces(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert main(["-C", str(root), "install"]) == 0
    doorway = root / ".claude" / "skills" / "giro" / "SKILL.md"
    doorway.write_text("TUNED BY USER")
    assert main(["-C", str(root), "install"]) == 0  # kept without --force
    assert doorway.read_text() == "TUNED BY USER"
    assert main(["-C", str(root), "install", "--force"]) == 0  # restored with --force
    assert doorway.read_text() != "TUNED BY USER"


def test_install_custom_dest(tmp_path):
    dest = tmp_path / "elsewhere"
    assert main(["-C", str(tmp_path), "install", "--dest", str(dest)]) == 0
    assert (dest / "spec" / "SKILL.md").is_file()
    assert not (dest / "worker").exists()  # still host-skills-only
    agents = tmp_path / ".agents" / "skills"
    assert agents.is_symlink()
    assert (agents / "spec" / "SKILL.md").resolve() == (dest / "spec" / "SKILL.md").resolve()


# -- installed-skill drift ----------------------------------------------------


def _installed(root):
    return root / ".claude" / "skills"


def _mark_placed(dest, name):
    """Rewrite the manifest entry to the copy's current content — as if this
    exact content is what an (older) install placed."""
    path = dest / ".giro-install.json"
    manifest = json.loads(path.read_text())
    manifest["skills"][name] = skill_digest(dest / name)
    path.write_text(json.dumps(manifest))


def test_no_install_dir_is_not_drift(tmp_path):
    assert installed_skill_drift(tmp_path) == []


def test_fresh_install_is_clean_and_writes_the_manifest(tmp_path):
    assert main(["-C", str(tmp_path), "install"]) == 0
    assert (_installed(tmp_path) / ".giro-install.json").is_file()
    assert installed_skill_drift(tmp_path) == []


def test_edited_copy_is_customized_not_stale(tmp_path):
    assert main(["-C", str(tmp_path), "install"]) == 0
    (_installed(tmp_path) / "spec" / "SKILL.md").write_text("TUNED BY USER")
    assert installed_skill_drift(tmp_path) == [{"name": "spec", "state": "customized"}]


def test_unedited_copy_from_an_older_giro_is_stale(tmp_path):
    assert main(["-C", str(tmp_path), "install"]) == 0
    dest = _installed(tmp_path)
    (dest / "spec" / "SKILL.md").write_text("WHAT AN OLDER GIRO BUNDLED")
    _mark_placed(dest, "spec")  # copy and manifest agree; bundled has moved on
    assert installed_skill_drift(tmp_path) == [{"name": "spec", "state": "stale"}]


def test_differing_copy_without_a_manifest_is_unknown(tmp_path):
    assert main(["-C", str(tmp_path), "install"]) == 0
    dest = _installed(tmp_path)
    (dest / "spec" / "SKILL.md").write_text("PRE-MANIFEST EDIT")
    (dest / ".giro-install.json").unlink()
    assert installed_skill_drift(tmp_path) == [{"name": "spec", "state": "unknown"}]


def test_absent_skill_in_an_existing_install_is_missing(tmp_path):
    assert main(["-C", str(tmp_path), "install"]) == 0
    shutil.rmtree(_installed(tmp_path) / "giro-setup")
    assert installed_skill_drift(tmp_path) == [{"name": "giro-setup", "state": "missing"}]


def test_skip_existing_install_does_not_adopt_an_edited_copy(tmp_path):
    assert main(["-C", str(tmp_path), "install"]) == 0
    (_installed(tmp_path) / "spec" / "SKILL.md").write_text("TUNED BY USER")
    assert main(["-C", str(tmp_path), "install"]) == 0  # kept, manifest untouched
    assert installed_skill_drift(tmp_path) == [{"name": "spec", "state": "customized"}]
    assert main(["-C", str(tmp_path), "install", "--force"]) == 0  # restored, re-recorded
    assert installed_skill_drift(tmp_path) == []


def test_status_reports_skill_drift(project):
    assert status_report(project)["skill_drift"] == []  # never opted in: no drift
    assert main(["-C", str(project), "install"]) == 0
    (_installed(project) / "plan" / "SKILL.md").write_text("TUNED BY USER")
    assert status_report(project)["skill_drift"] == [{"name": "plan", "state": "customized"}]


def test_status_prints_a_drift_warning(tmp_path, capsys):
    assert main(["-C", str(tmp_path), "install"]) == 0
    dest = _installed(tmp_path)
    (dest / "spec" / "SKILL.md").write_text("WHAT AN OLDER GIRO BUNDLED")
    _mark_placed(dest, "spec")
    (_installed(tmp_path) / "plan" / "SKILL.md").write_text("TUNED BY USER")
    capsys.readouterr()  # drop the install command's own output
    _print_status({"specs": [], "needs_human": [], "skill_drift": installed_skill_drift(tmp_path)})
    out = capsys.readouterr().out
    assert "drifted" in out and "spec" in out and "--force" in out
    assert "plan" not in out  # customized copies are overrides, not warnings


# -- `giro prompts` CLI -------------------------------------------------------


def test_giro_prompts_lists_the_four_engine_prompts(tmp_path, capsys):
    assert main(["-C", str(tmp_path), "prompts"]) == 0
    out = capsys.readouterr().out
    names = {line.split("\t", 1)[0] for line in out.strip().splitlines() if line}
    assert names == {"worker", "planner", "review", "conformance"}


def test_giro_prompts_prints_a_named_prompts_body(tmp_path, capsys):
    assert main(["-C", str(tmp_path), "prompts", "worker"]) == 0
    body = capsys.readouterr().out
    assert "worker discipline" in body.lower()


def test_giro_prompts_unknown_name_errors(tmp_path, capsys):
    assert main(["-C", str(tmp_path), "prompts", "no-such-prompt"]) == 1
    err = capsys.readouterr().err
    assert "no-such-prompt" in err


def test_giro_prompts_project_override_wins(tmp_path, capsys):
    (tmp_path / "prompts").mkdir(parents=True)
    (tmp_path / "prompts" / "worker.md").write_text("PROJECT OVERRIDE\n")
    assert main(["-C", str(tmp_path), "prompts", "worker"]) == 0
    assert "PROJECT OVERRIDE" in capsys.readouterr().out
