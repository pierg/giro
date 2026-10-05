"""Config parsing, roster fallback (a judge never inherits bypass args), the
migration errors that reject retired gate/role names loudly, and the engine
preflight that rejects unresolvable judge criteria before token one."""

import pytest

from giro.config import ConfigError, load_config
from giro.drivers import FakeDriver
from giro.loops import Engine
from giro.store import Store
from giro.workspace import Workspace

# All configs must define [validate] (F8) — every test that just wants to
# check something else still needs at least one validate gate defined.
_VALIDATE = "[validate]\ngates = [ { name = \"v\", type = \"judge\", rubric = \"ok\" } ]\n"


def _write(root, toml):
    (root / "giro.toml").write_text(toml + _VALIDATE)
    return load_config(root)


def test_roster_fallback_inherits_driver_and_model_but_not_args(tmp_path):
    bypass = "--dangerously-bypass-approvals-and-sandbox"
    cfg = _write(
        tmp_path,
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
        "[runner.roster]\n"
        f'worker = {{ driver = "codex", model = "big", args = ["{bypass}"] }}\n',
    )
    judge = cfg.roster_for("judge")  # not in roster -> falls back to worker
    assert judge.driver == "codex" and judge.model == "big"
    assert judge.args == []  # the bypass flag is NOT inherited by the read-only judge
    # an explicit entry is honored verbatim
    assert cfg.roster_for("worker").args == [bypass]


def test_context_timeout_parsed_with_default(tmp_path):
    cfg = _write(
        tmp_path, '[verify]\ngates = [ { name = "t", type = "command", run = "true" } ]\n'
    )
    assert cfg.context_timeout == 3600  # default
    cfg2 = _write(
        tmp_path,
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
        "[budget]\ncontext_timeout = 900\n",
    )
    assert cfg2.context_timeout == 900


def test_preflight_rejects_unresolvable_judge_criterion(tmp_path):
    cfg = _write(
        tmp_path,
        "[verify]\n"
        'gates = [ { name = "review", type = "judge", criterion = "no-such-criterion" } ]\n'
        # override the default validate gate with one whose criterion resolves
        # (an inline rubric) — the preflight failure we want to catch is on
        # the verify gate specifically.
        ,
    )
    engine = Engine(
        cfg=cfg,
        store=Store(tmp_path),
        workspace=Workspace(tmp_path),
        drivers={"worker": FakeDriver([]), "judge": FakeDriver([]), "planner": FakeDriver([])},
    )
    with pytest.raises(ConfigError, match="no-such-criterion"):
        engine._preflight()


# -- migration errors for retired gate types and roster roles ----------------


def test_retired_prompt_gate_type_is_rejected_with_migration_hint(tmp_path):
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "review", type = "prompt", rubric = "ok?" } ]\n'
        + _VALIDATE
    )
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    message = str(exc.value).lower()
    assert "retired" in message and "type='judge'" in message


def test_retired_skill_gate_type_is_rejected_with_criterion_hint(tmp_path):
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "review", type = "skill", skill = "review" } ]\n'
        + _VALIDATE
    )
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    message = str(exc.value).lower()
    assert "retired" in message and "type='judge'" in message and "criterion" in message


def test_judge_gate_with_both_rubric_and_criterion_is_rejected(tmp_path):
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "review", type = "judge", rubric = "r", criterion = "review" } ]\n'
        + _VALIDATE
    )
    with pytest.raises(ConfigError, match="specify one of 'rubric' or 'criterion'"):
        load_config(tmp_path)


def test_judge_gate_with_neither_rubric_nor_criterion_is_rejected(tmp_path):
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "review", type = "judge" } ]\n'
        + _VALIDATE
    )
    with pytest.raises(ConfigError, match="'criterion' or 'rubric'"):
        load_config(tmp_path)


def test_legacy_implementer_role_is_accepted_with_deprecation_notice(tmp_path, capsys):
    cfg = _write(
        tmp_path,
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
        "[runner.roster]\n"
        'implementer = { driver = "codex", model = "big" }\n',
    )
    entry = cfg.roster_for("worker")
    assert entry.driver == "codex" and entry.model == "big"
    err = capsys.readouterr().err
    assert "implementer' is renamed to 'worker'" in err


def test_missing_validate_section_is_rejected(tmp_path):
    """F8 — `[validate]` without gates means all([]) is True, so every Spec
    would validate silently. Refuse the config with a clear message."""
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
    )
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    assert "[validate]" in str(exc.value)
    assert "at least one gate" in str(exc.value)


def test_empty_validate_gates_is_rejected(tmp_path):
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
        "[validate]\n"
        "gates = []\n"
    )
    with pytest.raises(ConfigError, match=r"\[validate\]"):
        load_config(tmp_path)


# -- U7: unknown-key warnings on stderr ---------------------------------------


def test_unknown_top_level_key_warns_but_parses(tmp_path, capsys):
    """A typo like [runenr] at the top level is loud but not fatal — parsing
    still succeeds; a stderr warning tells the human what got ignored."""
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
        "[validate]\n"
        'gates = [ { name = "v", type = "judge", rubric = "ok" } ]\n'
        "[runenr]\nconcurrency = 3\n"
    )
    load_config(tmp_path)
    err = capsys.readouterr().err
    assert "runenr" in err and "unknown key" in err


def test_unknown_runner_key_warns(tmp_path, capsys):
    """`concurrancy` on the runner table is the typo this catches — silent
    acceptance meant the misspelt value was ignored while the correct name
    kept its default."""
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
        "[validate]\n"
        'gates = [ { name = "v", type = "judge", rubric = "ok" } ]\n'
        "[runner]\nconcurrancy = 3\n"
    )
    load_config(tmp_path)
    err = capsys.readouterr().err
    assert "concurrancy" in err and "[runner]" in err


def test_unknown_roster_role_warns(tmp_path, capsys):
    """`reviewer = { ... }` — a stale role name silently ignored today gets a
    stderr warning; the config still parses."""
    (tmp_path / "giro.toml").write_text(
        "[verify]\n"
        'gates = [ { name = "t", type = "command", run = "true" } ]\n'
        "[validate]\n"
        'gates = [ { name = "v", type = "judge", rubric = "ok" } ]\n'
        "[runner.roster]\n"
        'reviewer = { driver = "claude" }\n'
    )
    load_config(tmp_path)
    err = capsys.readouterr().err
    assert "reviewer" in err and "runner.roster" in err
