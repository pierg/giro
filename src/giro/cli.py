"""The giro CLI — the product's front door.

Exit codes are part of the contract: 0 = proof, 2 = needs-human, 1 = error.
A CI job or a chat agent reads them the same way.

A Run is either held in the foreground — where those codes are its answer — or
dispatched with ``--detach``, where the answer lands in its Ledger instead and
``giro runs`` / ``giro logs`` are how anyone asks for it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .config import CONFIG_NAME, INIT_TEMPLATE, ConfigError, load_config
from .drivers import DriverError, build_driver
from .envelope import EnvelopeError
from .gates import build_context, run_gates
from .ledger import (
    Ledger,
    RunRecord,
    list_runs,
    live_runs_by_spec,
    load_run,
    read_events,
    render_event,
)
from .loops import Engine
from .projection import ProjectionError, build_projection
from .prompts import bundled_dir, installed_skill_drift, record_placement, resolve_prompt
from .reconcile import build_reconciler
from .runs import RunError, refuse_if_locked
from .scaffold import ISSUE_BODY, SPEC_BODY, write_adr
from .states import ISSUE_TERMINAL
from .store import Spec, SpecView, Store, StoreError, slugify
from .workspace import Workspace, WorkspaceError

EXIT_PROOF = 0
EXIT_ERROR = 1
EXIT_NEEDS_HUMAN = 2

FOLLOW_POLL = 0.3  # seconds between reads while following a live Run


def _build_engine(root: Path) -> Engine:
    cfg = load_config(root)
    drivers = {}
    for role in ("worker", "judge", "planner"):
        entry = cfg.roster_for(role)
        drivers[role] = build_driver(entry.driver, args=entry.args, timeout=cfg.context_timeout)
    workspace = Workspace(root)
    return Engine(
        cfg=cfg,
        store=Store(root),
        workspace=workspace,
        drivers=drivers,
        projection=build_projection(cfg, workspace),
    )


def cmd_init(root: Path) -> int:
    path = root / CONFIG_NAME
    if path.exists():
        print(f"{CONFIG_NAME} already exists — edit it directly.")
        return EXIT_ERROR
    path.write_text(INIT_TEMPLATE, encoding="utf-8")
    (root / "docs" / "specs").mkdir(parents=True, exist_ok=True)
    print(f"Wrote {CONFIG_NAME} and docs/specs/.")
    print(
        "Next: this is a non-interactive starter — for a config that fits this repo, "
        "run `giro install` and drive setup from chat: the `giro-setup` skill inspects the "
        "repo and writes a real giro.toml, then the `spec` skill captures a Spec and `giro` "
        "implements it.")
    return EXIT_PROOF


def cmd_new_spec(root: Path, name: str) -> int:
    store = Store(root)
    slug = slugify(name)
    title = name if name != slug else slug.replace("-", " ").title()
    spec = store.create_spec(slug, f"# {title}\n\n{SPEC_BODY}")
    print(f"created {spec.path.relative_to(root)} [draft]")
    print("Write the body (the `spec` skill has the template), then:")
    print(f'  giro new issue {slug} "<title>"    # optional: plan the slices yourself')
    print(f"  giro implement {slug}              # or let the engine plan")
    return EXIT_PROOF


def cmd_new_issue(root: Path, spec_slug: str, title: str, blocked_by: list[str]) -> int:
    """Scaffold a new Issue where the Spec actually lives.

    A draft Spec still lives in the checkout — the Issue lands there. A Spec
    that has activated lives on ``giro/<slug>`` (ADR-0013): the Issue is
    written into the Spec's persistent worktree and committed onto its branch,
    so ``giro status`` and every next Run see it. The checkout's copy of the
    Spec, if any, is stale by design and left alone.
    """
    workspace = Workspace(root)
    view = SpecView(Store(root), workspace)
    if view.load_spec(spec_slug) is None:
        raise StoreError(f"no spec named {spec_slug!r} — create it with `giro new spec`")

    existing = [i for i in view.load_issues(spec_slug)]
    known = {i.id for i in existing}

    # Accept bare numeric ids in --blocked-by: "01" resolves to "01-…" when
    # unambiguous, matching how `giro implement <slug>/01` resolves.
    resolved: list[str] = []
    for dep in blocked_by:
        if dep in known:
            resolved.append(dep)
            continue
        if dep.isdigit():
            prefix = f"{int(dep):02d}-"
            matches = [i.id for i in existing if i.id.startswith(prefix)]
            if len(matches) == 1:
                resolved.append(matches[0])
                continue
            if len(matches) > 1:
                raise StoreError(
                    f"--blocked-by {dep!r} is ambiguous: {', '.join(matches)}"
                )
        # unknown: fall through to the loud error below with the raw name
        resolved.append(dep)

    unknown = [d for d in resolved if d not in known]
    if unknown:
        raise StoreError(
            f"--blocked-by references unknown issue(s): {', '.join(unknown)} "
            f"(existing: {', '.join(sorted(known)) or 'none'})"
        )

    on_branch = view.on_branch(spec_slug)
    if on_branch:
        ws, _ = workspace.ensure_spec_worktree(spec_slug)
        store = Store(ws.root)
        issue = store.create_issue(
            spec_slug, title, ISSUE_BODY, blocked_by=resolved
        )
        rel = f"docs/specs/{spec_slug}"
        ws.commit_within(rel, f"giro({spec_slug}): scaffold issue {issue.id}")
        deps = f" blocked_by={','.join(resolved)}" if resolved else ""
        print(f"created {issue.path.relative_to(ws.root)} [ready]{deps}")
        print(f"  on branch giro/{spec_slug} (worktree {ws.root})")
        return EXIT_PROOF

    store = Store(root)
    issue = store.create_issue(
        spec_slug, title, ISSUE_BODY, blocked_by=resolved
    )
    deps = f" blocked_by={','.join(resolved)}" if resolved else ""
    print(f"created {issue.path.relative_to(root)} [ready]{deps}")
    return EXIT_PROOF


def cmd_new_adr(root: Path, title: str) -> int:
    path = write_adr(root, title)
    print(f"created {path.relative_to(root)}")
    return EXIT_PROOF


def status_report(root: Path) -> dict:
    """What giro knows, read where each Spec's truth lives — the tip of
    ``giro/<slug>`` when that branch exists, the checkout when it does not.

    One structure serves both faces: the human's listing and the machine's
    JSON. A skill or a CI job reads the same states, attempts, blocking edges,
    and branch drift a person sees, without parsing prose.

    Ledger liveness is overlaid on top: a Spec with a live Run says so, and
    where that Run has got to. It is an overlay and nothing more — every state
    below it still comes from the markdown, so a deleted Ledger costs the
    reader liveness and nothing else.
    """
    workspace = Workspace(root)
    view = SpecView(Store(root), workspace)
    live = live_runs_by_spec(workspace.runtime_path())
    specs: list[dict] = []
    waiting: list[str] = []
    for spec in view.list_specs():
        on_branch = view.on_branch(spec.slug)
        drift = (
            workspace.ahead_behind(f"giro/{spec.slug}", spec.base_branch)
            if on_branch and spec.base_branch
            else None
        )
        issues = view.load_issues(spec.slug)
        known = {i.id for i in issues}
        if spec.state == "needs-human":
            waiting.append(spec.slug)
        entry = {
            "slug": spec.slug,
            "title": spec.title,
            "state": spec.state,
            "source": "branch" if on_branch else "checkout",
            "branch": f"giro/{spec.slug}" if on_branch else None,
            "worktree": str(workspace.spec_worktree_path(spec.slug)) if on_branch else None,
            "base_branch": spec.base_branch or None,
            "ahead": drift[0] if drift else None,
            "behind": drift[1] if drift else None,
            "gap_cycles": spec.gap_cycles,
            "run": _run_overlay(live.get(spec.slug)),
            "issues": [],
        }
        for issue in issues:
            if issue.state == "needs-human":
                waiting.append(issue.ref)
            entry["issues"].append(
                {
                    "id": issue.id,
                    "ref": issue.ref,
                    "title": issue.title,
                    "state": issue.state,
                    "attempts": issue.attempts,
                    "blocked_by": issue.blocked_by,
                    "unknown_blocked_by": [d for d in issue.blocked_by if d not in known],
                }
            )
        specs.append(entry)
    return {"specs": specs, "needs_human": waiting, "skill_drift": installed_skill_drift(root)}


def _run_overlay(run: RunRecord | None) -> dict | None:
    """The live Run on a Spec, as status reports it."""
    if run is None:
        return None
    return {
        "id": run.id,
        "target": run.target,
        "detached": run.detached,
        "pid": run.pid,
        "started": run.started,
        "phase": run.phase,
        "wave": run.wave,
        "issue": run.issue,
        "attempt": run.attempt,
    }


def _print_status(report: dict) -> None:
    if not report["specs"]:
        print("No specs under docs/specs/.")
        _print_skill_drift(report["skill_drift"])
        return
    # Specs that escalated with NO unfinished child Issue — the answer target
    # is the Spec itself. When a Spec is needs-human because its Issues are
    # (unfinished, budget-exhausted, worker-escalated), the answer target is
    # the ISSUE — the Spec's own body has nothing new to say, and re-invoking
    # the Spec would re-drive every green Issue too.
    escalated_specs: list[tuple[str, str]] = []
    escalated_issues: list[tuple[str, str]] = []
    for spec in report["specs"]:
        mark = " ⚠" if spec["state"] == "needs-human" else ""
        print(f"{spec['slug']} [{spec['state']}]{mark} {spec['title']}{_branch_note(spec)}")
        if run := spec["run"]:
            print(f"  live run {run['id']} — {run['phase']}  (giro logs {run['id']})")
        # A Spec that has a branch lives in its worktree — that is where the human
        # answers an escalation; the checkout's copy is stale or absent.
        home = f"{spec['worktree']}/" if spec["worktree"] else ""
        # A Spec escalates for two reasons: an unfinished child (any Issue
        # that's not in a terminal state — needs-human, in-progress, ready)
        # or a Validate budget with every child green. Only the second one
        # points at the Spec; the first points at each Issue that carries the
        # real answer target.
        unfinished_children = [
            i for i in spec["issues"] if i["state"] not in ISSUE_TERMINAL
        ]
        if spec["state"] == "needs-human" and not unfinished_children:
            escalated_specs.append((spec["slug"], home))
        for issue in spec["issues"]:
            deps = f" blocked_by={','.join(issue['blocked_by'])}" if issue["blocked_by"] else ""
            unknown = issue["unknown_blocked_by"]
            mark = " ⚠" if issue["state"] == "needs-human" or unknown else ""
            print(
                f"  {issue['id']} [{issue['state']}]{mark} "
                f"attempts={issue['attempts']}{deps} {issue['title']}"
            )
            if unknown:
                print(f"    ⚠ blocked_by references unknown issue(s): {', '.join(unknown)}")
            if issue["state"] == "needs-human":
                escalated_issues.append((issue["ref"], home))
    if escalated_specs or escalated_issues:
        print()
        print("needs-human — the loop is waiting on you:")
        for slug, home in escalated_specs:
            print(
                f"  {slug}: spec escalated — read {home}docs/specs/{slug}/SPEC.md, then re-run "
                f"`giro implement {slug}` (re-invoking is the answer; the budget resets)"
            )
        for ref, home in escalated_issues:
            print(f"  {ref}: read {home}docs/specs/{ref.replace('/', '/issues/')}.md,")
        if escalated_issues:
            print("  answer by editing the Issue body, then re-run `giro implement <ref>` —")
            print("  re-invoking that ISSUE is the answer (not the Spec); the budget resets.")
    _print_skill_drift(report["skill_drift"])


def _print_skill_drift(entries: list[dict]) -> None:
    """Warn when installed host skills no longer match this giro's bundled
    copies. Customized copies are overrides by design — the JSON reports them,
    but there is nothing to nag about."""
    notes = {
        "missing": "not installed — `giro install` places it",
        "stale": "an older giro placed this copy — `giro install --force` refreshes it",
        "unknown": "differs from bundled with no install record — "
        "`giro install --force` restores it, or keep it if the edit is deliberate",
    }
    actionable = [e for e in entries if e["state"] in notes]
    if not actionable:
        return
    print()
    print("⚠ .claude/skills has drifted from this giro's bundled skills:")
    for e in actionable:
        print(f"  {e['name']}: {notes[e['state']]}")


def _branch_note(spec: dict) -> str:
    """Where the line came from, and how far that branch has drifted from the
    base it will merge into."""
    if not spec["branch"]:
        return ""
    if spec["ahead"] is None:
        return f"  ({spec['branch']})"
    return f"  ({spec['branch']} +{spec['ahead']}/-{spec['behind']} vs {spec['base_branch']})"


def cmd_status(root: Path, as_json: bool) -> int:
    report = status_report(root)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_status(report)
    return EXIT_NEEDS_HUMAN if report["needs_human"] else EXIT_PROOF


def cmd_install(root: Path, dest: str | None, force: bool) -> int:
    # Only host skills are placed for discovery. Engine prompts (prompts/) stay
    # bundled — the engine resolves them itself and they are never
    # chat-invokable, so a host must not surface them.
    source = bundled_dir("skills")
    if source is None:
        print("error: no bundled skills found in this installation", file=sys.stderr)
        return EXIT_ERROR
    dest_dir = Path(dest).resolve() if dest else root / ".claude" / "skills"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(p.name for p in source.iterdir() if (p / "SKILL.md").is_file()):
        target = dest_dir / name
        if target.exists() and not force:
            print(f"kept    {target}  (exists — use --force to overwrite)")
            continue
        shutil.copytree(source / name, target, dirs_exist_ok=True)
        record_placement(dest_dir, name)
        print(f"placed  {target}")
    _project_agents_skills(root, dest_dir)
    print("Host skills placed. Slash/$-invocation now works wherever this directory is discovered.")
    return EXIT_PROOF


def _project_agents_skills(root: Path, dest_dir: Path) -> None:
    """Codex reads `.agents/skills`. Point that path at the copies `giro install` hashed,
    so Cursor and Codex see the same files Claude Code does and nothing is edited twice."""
    link = root / ".agents" / "skills"
    rel = os.path.relpath(dest_dir, link.parent)
    if link.is_symlink() and os.readlink(link) == rel:
        return
    if link.is_dir() and not link.is_symlink():
        print(f"kept    {link}  (a real directory — not replaced with a link to {dest_dir})")
        return
    if link.is_symlink() or link.is_file():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(rel)
    print(f"linked  {link} -> {rel}")


ENGINE_PROMPTS = ("worker", "planner", "review", "conformance")


def cmd_prompts(root: Path, name: str) -> int:
    """List resolved prompts, or print one — what the engine would actually inject.

    Same override order the engine follows: a project ``prompts/<name>.md``
    wins over the bundled default.
    """
    if not name:
        for prompt_name in ENGINE_PROMPTS:
            candidate = root / "prompts" / f"{prompt_name}.md"
            if candidate.is_file():
                origin = str(candidate)
            elif resolve_prompt(root, "prompts", prompt_name) is not None:
                origin = "bundled"
            else:
                origin = "missing"
            print(f"{prompt_name}\t{origin}")
        return EXIT_PROOF
    body = resolve_prompt(root, "prompts", name)
    if body is None:
        print(f"error: prompt {name!r} not found in prompts/ (project or bundled)", file=sys.stderr)
        return EXIT_ERROR
    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n")
    return EXIT_PROOF


def cmd_verify(root: Path) -> int:
    engine = _build_engine(root)
    engine._preflight()  # reject unresolvable judge criteria before spawning a judge
    material = engine.workspace.working_tree_diff()
    ctx = build_context(engine.cfg, root, material, engine.drivers["judge"])
    green, findings = run_gates(engine.cfg.verify_gates, ctx)
    for finding in findings:
        print(f"[{finding.gate}] {finding.summary}")
        if finding.detail:
            print(f"    {finding.detail[:500]}")
    print("all green" if green else f"{len(findings)} finding(s)")
    return EXIT_PROOF if green else EXIT_NEEDS_HUMAN


def _doctor_check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def _doctor_config(root: Path) -> tuple[list[dict], object]:
    """Config file present, parses, judge criteria resolve, roster roles known."""
    from .prompts import resolve_prompt

    checks: list[dict] = []
    path = root / CONFIG_NAME
    if not path.is_file():
        checks.append(_doctor_check("giro.toml", False, f"missing at {path} — run `giro init`"))
        return checks, None
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        checks.append(_doctor_check("giro.toml", False, f"parse error: {exc}"))
        return checks, None
    checks.append(_doctor_check("giro.toml", True, "parses"))

    unresolved: list[str] = []
    for gate in (*cfg.verify_gates, *cfg.validate_gates):
        if gate.type != "judge" or not gate.criterion:
            continue
        if resolve_prompt(root, "prompts", gate.criterion) is None:
            unresolved.append(f"{gate.name}:{gate.criterion}")
    if unresolved:
        checks.append(_doctor_check(
            "verify/validate criteria", False,
            f"unresolvable in prompts/: {', '.join(unresolved)}",
        ))
    else:
        checks.append(_doctor_check(
            "verify/validate criteria", True,
            f"{len(cfg.verify_gates)} verify + {len(cfg.validate_gates)} validate",
        ))

    unknown_roles = sorted(r for r in cfg.roster if r not in ("worker", "judge", "planner"))
    if unknown_roles:
        checks.append(_doctor_check(
            "roster roles", False, f"unknown role(s): {', '.join(unknown_roles)}",
        ))
    else:
        checks.append(_doctor_check(
            "roster roles", True,
            f"{', '.join(sorted(cfg.roster)) or 'defaults (all roles inherit worker)'}",
        ))
    return checks, cfg


def _doctor_drivers(cfg) -> list[dict]:
    """Each role's driver CLI is on PATH — the same check preflight makes."""
    from .drivers import DRIVER_REGISTRY

    checks: list[dict] = []
    for role in ("worker", "judge", "planner"):
        entry = cfg.roster_for(role)
        if entry.driver not in DRIVER_REGISTRY:
            checks.append(_doctor_check(
                f"{role} driver", False,
                f"unknown driver {entry.driver!r} (known: {', '.join(sorted(DRIVER_REGISTRY))})",
            ))
            continue
        path = shutil.which(entry.driver)
        if path is None:
            checks.append(_doctor_check(
                f"{role} driver", False,
                f"{entry.driver!r} not on PATH — install its CLI",
            ))
        else:
            checks.append(_doctor_check(f"{role} driver", True, f"{entry.driver} at {path}"))
    return checks


def _doctor_git(root: Path) -> list[dict]:
    """Git repo, has commits, HEAD on a branch. Uncommitted files is informational."""
    ws = Workspace(root)
    checks: list[dict] = []
    if not ws.is_repo():
        checks.append(_doctor_check("git repo", False, f"{root} is not a git repository"))
        return checks
    checks.append(_doctor_check("git repo", True, "yes"))
    if not ws.has_commits():
        checks.append(_doctor_check("commits", False, "no commits yet — make the first one"))
        return checks
    checks.append(_doctor_check("commits", True, "HEAD resolves"))
    branch = ws.current_branch()
    if branch:
        checks.append(_doctor_check("branch", True, f"on {branch}"))
    else:
        checks.append(_doctor_check(
            "branch", False, "detached HEAD — check out a branch",
        ))
    try:
        dirty = ws._git("status", "--porcelain").stdout.strip()
    except WorkspaceError as exc:
        dirty = ""
        checks.append(_doctor_check("working tree", True, f"unknown ({exc})"))
    else:
        if dirty:
            files = dirty.count("\n") + 1
            checks.append(_doctor_check(
                "working tree", True, f"{files} uncommitted file(s) — informational",
            ))
        else:
            checks.append(_doctor_check("working tree", True, "clean"))
    return checks


def _doctor_specs(root: Path) -> list[dict]:
    """The fail-closed boundary: every Spec and Issue on disk parses, and every
    ``blocked_by`` edge resolves to a real Issue with no cycle.

    Authoring artifacts can be born without giro — by the ``spec``/``plan``
    skills, or by hand — so before a Run trusts them, ``doctor`` reads them the
    way the engine will and reports any it would choke on. A repo with no Specs
    is not a failure: it is ready to author, not yet ready to run.
    """
    view = SpecView(Store(root), Workspace(root))
    slugs = view.spec_slugs()
    if not slugs:
        return [_doctor_check("specs", True, "none yet — author one with the `spec` skill")]
    specs = 0
    issues = 0
    for slug in slugs:
        try:
            view.load_spec(slug)
            spec_issues = view.load_issues(slug)
        except StoreError as exc:
            return [_doctor_check("specs", False, str(exc))]
        by_id = {i.id: i for i in spec_issues}
        for issue in spec_issues:
            dangling = [d for d in issue.blocked_by if d not in by_id]
            if dangling:
                return [_doctor_check(
                    "specs", False,
                    f"{issue.ref}: blocked_by references unknown issue(s) "
                    f"{', '.join(dangling)}",
                )]
        if (cycle := _first_cycle(by_id)) is not None:
            return [_doctor_check("specs", False, f"{slug}: blocking cycle {' → '.join(cycle)}")]
        specs += 1
        issues += len(spec_issues)
    detail = f"{specs} spec(s), {issues} issue(s) parse; edges resolve"
    return [_doctor_check("specs", True, detail)]


def _first_cycle(by_id: dict) -> list[str] | None:
    """The first blocking cycle among these Issues, as an id path, or None."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(by_id, WHITE)

    def walk(node: str, trail: list[str]) -> list[str] | None:
        color[node] = GREY
        for dep in by_id[node].blocked_by:
            if dep not in by_id:
                continue
            if color[dep] == GREY:
                return trail[trail.index(dep):] + [dep]
            if color[dep] == WHITE and (found := walk(dep, [*trail, dep])) is not None:
                return found
        color[node] = BLACK
        return None

    for start in by_id:
        if color[start] == WHITE and (found := walk(start, [start])) is not None:
            return found
    return None


def _doctor_projection(root: Path, cfg) -> list[dict]:
    """When [github_projection] enabled=true: gh on PATH, token from somewhere."""
    from .projection import ENV_FILE, TOKEN_VAR, load_token, read_env_file

    checks: list[dict] = []
    if not cfg.projection.enabled:
        checks.append(_doctor_check(
            "projection", True, "disabled (set [github_projection] enabled = true to render)",
        ))
        return checks
    checks.append(_doctor_check("projection", True, "enabled in giro.toml"))
    gh = shutil.which("gh")
    if gh is None:
        checks.append(_doctor_check("gh", False, "not on PATH — install the GitHub CLI"))
    else:
        checks.append(_doctor_check("gh", True, gh))
    token = read_env_file(root / ENV_FILE).get(TOKEN_VAR, "")
    if token:
        checks.append(_doctor_check("token", True, f"{ENV_FILE} ({TOKEN_VAR})"))
    elif os.environ.get(TOKEN_VAR):
        checks.append(_doctor_check("token", True, f"environment ({TOKEN_VAR})"))
    else:
        # load_token falls back to "gh login" — the projection uses gh's own
        # session when no explicit token is set. Not a failure, only informational.
        _, source = load_token(root)
        checks.append(_doctor_check("token", True, f"no {TOKEN_VAR} — using {source}"))
    return checks


def cmd_doctor(root: Path, as_json: bool) -> int:
    """Is this repository ready for giro? A one-look readiness report.

    Config, drivers, git, specs, projection — every check the engine leans on.
    Human output is one line per check; ``--json`` is the same tree a chat skill
    or a CI job reads. Exit 0 when every "must" check passes: config parses,
    every driver is on PATH, the repo has commits and a branch, and every Spec
    and Issue on disk parses with resolvable edges. Everything else is
    informational.
    """
    config_checks, cfg = _doctor_config(root)
    report = {"config": config_checks}
    driver_checks = _doctor_drivers(cfg) if cfg is not None else []
    git_checks = _doctor_git(root)
    spec_checks = _doctor_specs(root)
    projection_checks = _doctor_projection(root, cfg) if cfg is not None else []
    report["drivers"] = driver_checks
    report["git"] = git_checks
    report["specs"] = spec_checks
    report["projection"] = projection_checks

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for section in ("config", "drivers", "git", "specs", "projection"):
            for entry in report[section]:
                mark = "ok " if entry["ok"] else "FAIL"
                print(f"[{mark}] {section:<10} {entry['name']:<28} {entry['detail']}")

    must_config = all(c["ok"] for c in config_checks)
    must_drivers = bool(driver_checks) and all(c["ok"] for c in driver_checks)
    must_git = all(c["ok"] for c in git_checks)
    must_specs = all(c["ok"] for c in spec_checks)
    return (
        EXIT_PROOF
        if (must_config and must_drivers and must_git and must_specs)
        else EXIT_ERROR
    )


def cmd_project_check(root: Path, as_json: bool) -> int:
    """Is this repository ready to be projected onto? Exit 0 says yes.

    The checks run whatever ``[github_projection] enabled`` says — an operator
    proves the repository first and turns Projection on second — but readiness
    means "a Run would render onto GitHub", so a repository that is fine while
    Projection is off still exits nonzero, saying which of the two is missing.
    """
    cfg = load_config(root)
    report = build_projection(cfg, Workspace(root)).check()
    if as_json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        for line in report.lines():
            print(line)
    return EXIT_PROOF if (report.ready and report.enabled) else EXIT_ERROR


def cmd_project_reconcile(root: Path, target: str, as_json: bool) -> int:
    """Reconverge GitHub on the markdown store — the Projection's undo for an
    outage, drift, or hand-meddling.

    One direction only: it reads the store and writes GitHub, never the other
    way about, and never the store, the branches, or anything else local. Exit
    0 says GitHub now says what the markdown says.
    """
    cfg = load_config(root)
    projection = build_projection(cfg, Workspace(root))
    report = build_reconciler(cfg, root, projection).run(target)
    if as_json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        for line in report.lines():
            print(line)
    return EXIT_PROOF if report.ok else EXIT_ERROR


def cmd_implement(root: Path, target: str, run_id: str = "", quiet: bool = False) -> int:
    """A Run held in the foreground: the exit code is its answer, and its
    Ledger records the same story a dispatched Run's would.

    The Ledger belongs to the engine — ``run_id`` is only the entry a dispatch
    minted for this process to claim — so however the Run ends, including
    before it has resolved its target, it ends in one place.

    ``quiet`` suppresses the stderr phase lines; the final stdout report and
    the on-disk Ledger are unchanged.
    """
    report = _build_engine(root).implement(target, run_id=run_id, quiet=quiet)
    print(f"{report.target}: {report.outcome} — {report.detail}")
    for ref, state in sorted(report.issues.items()):
        print(f"  {ref}: {state}")
    if report.outcome in ("done", "all-done"):
        return EXIT_PROOF
    return EXIT_NEEDS_HUMAN


def cmd_dispatch(root: Path, target: str, quiet: bool = False) -> int:
    """Start a detached Run and return the terminal at once.

    The Run is a plain child process in its own session, so it outlives this
    shell and the chat session that opened it. Nothing is queued and nothing
    is supervised: the Ledger is where it says what it is doing, and where its
    outcome waits to be read.

    What can be said loudly is said here, before the terminal is handed back:
    an unknown target and a Spec that already has a live Run both fail at the
    prompt rather than in a Ledger nobody has been told to read yet.
    """
    workspace = Workspace(root)
    resolved = SpecView(Store(root), workspace).resolve(target)
    spec = resolved if isinstance(resolved, Spec) else resolved[0]
    refuse_if_locked(workspace.runtime_dir(), spec.slug)
    ledger = Ledger.create(workspace.runtime_dir(), target, detached=True)
    argv = [sys.executable, "-m", "giro", "-C", str(root), "implement", target,
            "--run-id", ledger.id]
    if quiet:
        argv.append("--quiet")
    try:
        with open(ledger.console, "ab") as console:
            proc = subprocess.Popen(
                argv,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=console,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # survives the shell that dispatched it
            )
    except OSError as exc:
        ledger.finish("error", f"could not start a detached Run: {exc}")
        raise RunError(f"could not start a detached Run: {exc}") from exc
    ledger.mark(pid=proc.pid, state="running")
    print(f"dispatched {ledger.id} (pid {proc.pid}) — {target}")
    print(f"  follow it:  giro logs {ledger.id}")
    print("  every Run:  giro runs")
    return EXIT_PROOF


def cmd_runs(root: Path, as_json: bool, limit: int) -> int:
    """Live and recent Runs — what is working, and how the last ones ended."""
    runs = list_runs(Workspace(root).runtime_path())
    done = [r for r in runs if not r.live]
    listed = [r for r in runs if r.live] + (done[:limit] if limit > 0 else done)
    if as_json:
        print(json.dumps([_run_row(r) for r in listed], indent=2))
        return EXIT_PROOF
    if not listed:
        print("No runs recorded — dispatch one with `giro implement <target> --detach`.")
        return EXIT_PROOF
    width = max(len(r.id) for r in listed)
    for run in listed:
        detached = " detached" if run.detached else ""
        print(f"{run.id:<{width}}  {run.liveness:<8}{detached:<9}  {run.target}  {run.phase}")
    return EXIT_PROOF


def _run_row(run: RunRecord) -> dict:
    return {
        "id": run.id,
        "target": run.target,
        "spec": run.spec,
        "branch": run.branch,
        "liveness": run.liveness,
        "phase": run.phase,
        "detached": run.detached,
        "pid": run.pid,
        "started": run.started,
        "finished": run.finished,
        "outcome": run.outcome,
        "detail": run.detail,
        "wave": run.wave,
        "issue": run.issue,
        "attempt": run.attempt,
    }


def cmd_logs(root: Path, run_id: str, lines: int, follow: bool, as_json: bool = False) -> int:
    """One Run's story: the tail of what has happened, then — while it is still
    alive — the rest as it happens.

    ``--json`` is the machine tail: raw JSONL from ``events.jsonl``, one object
    per line in ``seq`` order, with no preamble on stdout. Same ``--lines`` and
    ``--follow`` semantics as the human rendering.
    """
    runtime = Workspace(root).runtime_path()
    run = load_run(runtime, run_id)
    if not as_json:
        print(f"run {run.id} — {run.target} [{run.liveness}] {run.phase}")
    seen = max(len(read_events(run.dir)) - lines, 0) if lines > 0 else 0
    if seen and not as_json:
        print(f"… {seen} earlier event(s) — use `-n 0` for the whole story")

    def drain() -> None:
        """Print every event past the last one printed."""
        nonlocal seen
        events = read_events(run.dir)
        for event in events[seen:]:
            if as_json:
                print(json.dumps(event), flush=True)
            else:
                print(render_event(event), flush=True)
        seen = len(events)

    drain()
    while follow and load_run(runtime, run.id).live:
        time.sleep(FOLLOW_POLL)
        drain()
    if follow:
        drain()  # a Run writes its ending and only then says it is over
    return EXIT_PROOF


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="giro",
        description="Guarded loops: Specs become verified work — proof or escalation.",
    )
    parser.add_argument("--version", action="version", version=f"giro {__version__}")
    parser.add_argument(
        "-C", dest="root", default=".", help="project root (default: current directory)"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init", help="write a starter giro.toml")
    p_status = sub.add_parser("status", help="show spec and issue states")
    p_status.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="machine-readable status for a skill or a CI job",
    )
    sub.add_parser("verify", help="run the [verify] gate set once")
    p_doctor = sub.add_parser(
        "doctor", help="check readiness: config parses, drivers on PATH, git ready"
    )
    p_doctor.add_argument(
        "--json", action="store_true", dest="as_json",
        help="machine-readable report (config/drivers/git/projection lists)",
    )
    p_impl = sub.add_parser("implement", help="run the guarded loop for a spec or issue")
    p_impl.add_argument("target", help="spec slug, issue id, or slug/issue-id")
    p_impl.add_argument(
        "--detach",
        action="store_true",
        help="dispatch: return at once with a Run id; the Run outlives this shell",
    )
    p_impl.add_argument(
        "--run-id",
        default="",
        metavar="ID",
        help="write into an existing Ledger entry (how a dispatched Run claims its own)",
    )
    p_impl.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the stderr phase lines; the final report and the Ledger are unchanged",
    )
    p_runs = sub.add_parser("runs", help="list live and recent Runs")
    p_runs.add_argument("--json", action="store_true", dest="as_json", help="machine-readable")
    p_runs.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="how many finished Runs to list (0 = all); live Runs are always listed",
    )
    p_logs = sub.add_parser("logs", help="follow a live Run's story, or tail a finished one")
    p_logs.add_argument("run_id", nargs="?", default="", help="Run id (default: the newest)")
    p_logs.add_argument(
        "-n", "--lines", type=int, default=40, metavar="N",
        help="how many past events to print (0 = the whole story)",
    )
    p_logs.add_argument(
        "-f", "--follow", action="store_true", dest="follow",
        help="tail the Run: keep printing events as they arrive while the Run is live",
    )
    p_logs.add_argument(
        "--json", action="store_true", dest="as_json",
        help="machine tail: raw JSONL from events.jsonl, one object per line",
    )
    p_proj = sub.add_parser(
        "project",
        help="reconcile GitHub with the markdown store — one-way, fail-soft",
    )
    p_proj.add_argument(
        "target",
        nargs="?",
        default="",
        help="spec slug to reconcile (default: every spec a Run has worked on)",
    )
    p_proj.add_argument(
        "--check",
        action="store_true",
        help="report whether this repository is ready to be projected onto, and stop",
    )
    p_proj.add_argument(
        "--json", action="store_true", dest="as_json", help="machine-readable result"
    )
    p_inst = sub.add_parser("install", help="place the bundled skills into host discovery")
    p_inst.add_argument("--dest", help="skills directory (default: <root>/.claude/skills)")
    p_inst.add_argument("--force", action="store_true", help="overwrite existing skill folders")
    p_prompts = sub.add_parser(
        "prompts", help="list the engine-injected prompts, or print one's resolved body"
    )
    p_prompts.add_argument(
        "name",
        nargs="?",
        default="",
        help="prompt name (default: list every prompt and its origin)",
    )
    p_new = sub.add_parser(
        "new", help="scaffold a conformant Spec or Issue (the engine owns the template)"
    )
    new_sub = p_new.add_subparsers(dest="kind", required=True)
    p_ns = new_sub.add_parser("spec", help="scaffold docs/specs/<slug>/SPEC.md in draft")
    p_ns.add_argument("name", help="spec name or slug")
    p_ni = new_sub.add_parser("issue", help="scaffold a ready child Issue under a spec")
    p_ni.add_argument("spec", help="spec slug")
    p_ni.add_argument("title", help="issue title")
    p_ni.add_argument(
        "--blocked-by",
        action="append",
        default=[],
        dest="blocked_by",
        metavar="ISSUE_ID",
        help="issue id that must complete first (repeatable)",
    )
    p_na = new_sub.add_parser("adr", help="scaffold a numbered ADR under docs/adr/")
    p_na.add_argument("title", help="decision title")

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    # A missing `-C` directory is a caller mistake, not an engine bug: catch
    # it here and print a one-line error rather than an unfiltered traceback
    # from the first path operation the command happens to make.
    if not root.is_dir():
        print(f"error: no such directory: {args.root}", file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.command == "init":
            return cmd_init(root)
        if args.command == "status":
            return cmd_status(root, args.as_json)
        if args.command == "verify":
            return cmd_verify(root)
        if args.command == "doctor":
            return cmd_doctor(root, args.as_json)
        if args.command == "implement":
            if args.detach:
                if args.run_id:
                    raise RunError(
                        "--run-id names a Ledger entry a Run claims; --detach mints its own"
                    )
                return cmd_dispatch(root, args.target, quiet=args.quiet)
            return cmd_implement(root, args.target, args.run_id, quiet=args.quiet)
        if args.command == "runs":
            return cmd_runs(root, args.as_json, args.limit)
        if args.command == "logs":
            return cmd_logs(root, args.run_id, args.lines, args.follow, args.as_json)
        if args.command == "project":
            if args.check:
                return cmd_project_check(root, args.as_json)
            return cmd_project_reconcile(root, args.target, args.as_json)
        if args.command == "install":
            return cmd_install(root, args.dest, args.force)
        if args.command == "prompts":
            return cmd_prompts(root, args.name)
        if args.command == "new":
            if args.kind == "spec":
                return cmd_new_spec(root, args.name)
            if args.kind == "adr":
                return cmd_new_adr(root, args.title)
            return cmd_new_issue(root, args.spec, args.title, args.blocked_by)
        parser.print_help()
        return EXIT_PROOF
    except (
        ConfigError,
        StoreError,
        WorkspaceError,
        DriverError,
        EnvelopeError,
        RunError,
        ProjectionError,
        FileNotFoundError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        # A filesystem error (permissions, no such file) surfaces here rather
        # than as a raw traceback the shell has to interpret.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
