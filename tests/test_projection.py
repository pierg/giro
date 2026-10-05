"""Projection plumbing: configuration, the token, the fail-soft `gh` runner,
preflight (fatal when enabled), and `giro project --check`.

Nothing here touches GitHub: the `gh` boundary is scripted the way drivers
are, so every invocation the engine would make is asserted instead of sent.
"""

import subprocess

import pytest

from giro import cli
from giro import projection as proj
from giro.config import INIT_TEMPLATE, ProjectionSettings, load_config
from giro.drivers import FakeDriver
from giro.ledger import EVENT_TYPES, Ledger
from giro.projection import (
    ENV_FILE,
    LABELS,
    TOKEN_VAR,
    FakeGh,
    GhResult,
    Projection,
    ProjectionError,
    SubprocessGh,
    build_projection,
    load_token,
    parse_repo,
)
from giro.workspace import Workspace
from tests.conftest import MINIMAL_TOML, git, good_worker, make_engine

PROJECTION_TOML = MINIMAL_TOML + """
[github_projection]
enabled = true
repo = "acme/widgets"
"""

LABEL_NAMES = [name for name, _color, _description in LABELS]


def healthy_gh(existing: list[str] | None = None) -> FakeGh:
    """A `gh` that answers preflight happily, with ``existing`` labels present."""
    listed = ", ".join(f'{{"name": "{name}"}}' for name in existing or [])
    return FakeGh(
        {
            "--version": GhResult(0, "gh version 2.63.2"),
            "auth status": GhResult(0, "Logged in to github.com as octocat"),
            "repo view": GhResult(0, '{"nameWithOwner": "acme/widgets"}'),
            "label list": GhResult(0, f"[{listed}]"),
        }
    )


def projection_for(gh: FakeGh, **settings) -> Projection:
    """A Projection wired to a scripted `gh`, enabled unless said otherwise."""
    return Projection(
        settings=ProjectionSettings(enabled=settings.pop("enabled", True), **settings),
        gh=gh,
        repo="acme/widgets",
    )


# -- configuration -----------------------------------------------------------


def test_projection_is_off_by_default(tmp_path):
    (tmp_path / "giro.toml").write_text(MINIMAL_TOML)
    assert load_config(tmp_path).projection.enabled is False  # no [github_projection] at all
    (tmp_path / "giro.toml").write_text(INIT_TEMPLATE)
    assert load_config(tmp_path).projection.enabled is False  # nor in the starter config
    assert Projection().enabled is False  # the default Projection is nobody looking


def test_the_old_projection_section_is_a_loud_config_error(tmp_path):
    (tmp_path / "giro.toml").write_text(MINIMAL_TOML + "\n[projection]\nenabled = true\n")
    with pytest.raises(Exception, match=r"renamed to \[github_projection\]"):
        load_config(tmp_path)


def test_enabling_projection_names_the_repository_or_autodetects_the_origin(project):
    assert parse_repo("git@github.com:acme/widgets.git") == "acme/widgets"
    assert parse_repo("https://github.com/acme/widgets.git") == "acme/widgets"
    assert parse_repo("https://github.com/acme/widgets") == "acme/widgets"
    assert parse_repo("git@gitlab.com:acme/widgets.git") == ""

    (project / "giro.toml").write_text(MINIMAL_TOML + "\n[github_projection]\nenabled = true\n")
    git(project, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    autodetected = build_projection(load_config(project), Workspace(project))
    assert autodetected.repo == "acme/widgets"

    (project / "giro.toml").write_text(PROJECTION_TOML.replace("acme/widgets", "acme/other"))
    named = build_projection(load_config(project), Workspace(project))
    assert named.repo == "acme/other"  # config wins over the remote


def test_a_malformed_repository_is_refused_at_load(tmp_path):
    (tmp_path / "giro.toml").write_text(MINIMAL_TOML + '\n[github_projection]\nrepo = "widgets"\n')
    with pytest.raises(Exception, match="owner/name"):
        load_config(tmp_path)


# -- the token ---------------------------------------------------------------


def test_token_comes_from_the_env_file_and_reaches_gh_only_through_the_environment(
    project, monkeypatch
):
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    (project / ENV_FILE).write_text(f'# local secrets\n{TOKEN_VAR}="s3cret"\nOTHER=1\n')
    token, source = load_token(project)
    assert token == "s3cret" and ENV_FILE in source

    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(argv=argv, env=kwargs["env"], timeout=kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    SubprocessGh(timeout=7, token=token, cwd=project).run(["auth", "status"])

    assert captured["argv"] == ["gh", "auth", "status"]
    assert "s3cret" not in " ".join(captured["argv"])  # never on the command line
    assert captured["env"][TOKEN_VAR] == "s3cret"
    assert captured["timeout"] == 7  # every invocation carries a timeout


def test_no_token_falls_back_to_ghs_own_login(project, monkeypatch):
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    token, source = load_token(project)
    assert token == "" and source == "gh login"


# -- the fail-soft runner ----------------------------------------------------


def test_a_hanging_gh_call_answers_instead_of_raising(project, monkeypatch):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(proj.subprocess, "run", hang)
    result = SubprocessGh(timeout=3, cwd=project).run(["repo", "view"])
    assert not result.ok and "timed out after 3s" in result.stderr


def test_a_missing_gh_binary_answers_instead_of_raising(project, monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(proj.subprocess, "run", missing)
    result = SubprocessGh(cwd=project).run(["--version"])
    assert not result.ok and "not found on PATH" in result.stderr


def test_a_failing_call_records_a_ledger_event_and_never_raises(project):
    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    gh = FakeGh({"issue create": GhResult(1, stderr="HTTP 403")})
    projection = projection_for(gh)
    projection.attach(ledger)

    result = projection.call("issue", "create", "--title", "x")

    assert result.ok is False  # the caller is told, the loop is not interrupted
    recorded = [e for e in ledger.events() if e["type"] == "projection"]
    assert len(recorded) == 1
    assert recorded[0]["ok"] is False and "403" in recorded[0]["detail"]
    assert set(e["type"] for e in ledger.events()) <= set(EVENT_TYPES)


def test_calls_are_not_made_at_all_when_projection_is_off():
    gh = FakeGh()
    projection = projection_for(gh, enabled=False)
    result = projection.call("issue", "create")
    assert gh.calls == [] and not result.ok


# -- preflight ---------------------------------------------------------------


def test_preflight_ensures_the_label_set_idempotently_with_stable_colors():
    gh = healthy_gh(existing=["giro:spec"])
    report = projection_for(gh).preflight()

    assert report.ready
    created = [args for args in gh.calls if args[:2] == ["label", "create"]]
    assert [args[2] for args in created] == [n for n in LABEL_NAMES if n != "giro:spec"]
    for name, color, _description in LABELS:
        if name == "giro:spec":
            continue
        args = next(a for a in created if a[2] == name)
        assert args[args.index("--color") + 1] == color
        assert args[args.index("--repo") + 1] == "acme/widgets"

    settled = healthy_gh(existing=LABEL_NAMES)
    assert projection_for(settled).preflight().ready
    assert [a for a in settled.calls if a[:2] == ["label", "create"]] == []


def test_preflight_stops_at_the_first_failure_and_names_it():
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["repo view"] = GhResult(1, stderr="Could not resolve to a Repository")
    report = projection_for(gh).preflight()

    assert not report.ready
    assert "repo" in report.failure and "Repository" in report.failure
    states = {check.name: check.state for check in report.checks}
    assert states["gh"] == "ok" and states["repo"] == "failed" and states["labels"] == "skipped"
    assert [a for a in gh.calls if a[0] == "label"] == []  # never reached


def test_preflight_failure_refuses_the_run(project):
    """When [github_projection] enabled = true, a failing preflight is fatal:
    the operator asked for the surface, so a silent Run would be the wrong
    answer. The failure is recorded in the Ledger before the ProjectionError
    is raised, so the Run's story still names what went wrong.
    """
    ledger = Ledger.create(Workspace(project).runtime_dir(), "demo")
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["auth status"] = GhResult(1, stderr="not logged in")
    projection = projection_for(gh)
    projection.attach(ledger)

    with pytest.raises(ProjectionError, match="not logged in"):
        projection.start()

    recorded = [e for e in ledger.events() if e["type"] == "projection"]
    assert recorded and recorded[0]["action"] == "preflight"
    assert recorded[0]["ok"] is False


# -- giro project --check ----------------------------------------------------


def test_project_check_reports_readiness_in_both_forms(project, monkeypatch, capsys):
    (project / "giro.toml").write_text(PROJECTION_TOML)
    gh = healthy_gh(existing=LABEL_NAMES)
    monkeypatch.setattr(cli, "build_projection", lambda cfg, workspace: projection_for(gh))

    assert cli.main(["-C", str(project), "project", "--check"]) == 0
    human = capsys.readouterr().out
    assert "acme/widgets" in human and "ready" in human

    assert cli.main(["-C", str(project), "project", "--check", "--json"]) == 0
    machine = capsys.readouterr().out
    assert '"ready": true' in machine and '"repo": "acme/widgets"' in machine


def test_project_check_exits_nonzero_when_the_repository_is_not_ready(
    project, monkeypatch, capsys
):
    (project / "giro.toml").write_text(PROJECTION_TOML)
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["repo view"] = GhResult(1, stderr="Could not resolve to a Repository")
    monkeypatch.setattr(cli, "build_projection", lambda cfg, workspace: projection_for(gh))

    assert cli.main(["-C", str(project), "project", "--check", "--json"]) != 0
    assert '"ready": false' in capsys.readouterr().out


def test_project_check_runs_the_checks_even_when_projection_is_off(project, monkeypatch, capsys):
    (project / "giro.toml").write_text(PROJECTION_TOML.replace("enabled = true", "enabled = false"))
    gh = healthy_gh(existing=LABEL_NAMES)
    monkeypatch.setattr(
        cli, "build_projection", lambda cfg, workspace: projection_for(gh, enabled=False)
    )

    assert cli.main(["-C", str(project), "project", "--check"]) != 0
    out = capsys.readouterr().out
    assert "off" in out  # the repository is fine; the configuration is what is missing
    assert any(args[:2] == ["repo", "view"] for args in gh.calls)


# -- a Run, projected and not ------------------------------------------------


def test_projection_off_makes_no_gh_calls_and_changes_nothing(project):
    gh = FakeGh()
    engine = make_engine(
        project,
        worker=FakeDriver([good_worker]),
        projection=projection_for(gh, enabled=False),
    )
    report = engine.implement("demo/01-write-feature")

    assert report.outcome == "done"
    assert gh.calls == []


def test_a_healthy_projection_preflights_once_and_leaves_the_outcome_alone(project):
    gh = healthy_gh(existing=LABEL_NAMES)
    engine = make_engine(
        project, worker=FakeDriver([good_worker]), projection=projection_for(gh)
    )
    report = engine.implement("demo/01-write-feature")

    assert report.outcome == "done"  # identical to the projection-off Run
    verbs = [args[0] for args in gh.calls]
    assert verbs[:4] == ["--version", "auth", "repo", "label"]
    assert verbs.count("--version") == 1  # preflight once, before the loop leans on it
    # what follows is the Spec's surface — tests/test_projection_spec.py judges it


def test_runtime_gh_failure_leaves_the_outcome_alone(project):
    """Preflight passes but the content-creating calls a Run makes later
    (parent issue, sub-issues, comments, statuses) fail — the classic
    "GitHub had a bad afternoon" case. The loop's outcome must not depend on
    the tracker: fail-soft (ADR-0014) means the Ledger records what failed
    and the Run finishes on its own terms.
    """
    gh = healthy_gh(existing=LABEL_NAMES)
    gh.results["issue create"] = GhResult(1, stderr="upstream request timeout")
    engine = make_engine(
        project, worker=FakeDriver([good_worker]), projection=projection_for(gh)
    )
    report = engine.implement("demo/01-write-feature")

    assert report.outcome == "done"
    assert engine.projection.enabled is True  # preflight was fine — this was runtime
