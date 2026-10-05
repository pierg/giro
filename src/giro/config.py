"""giro.toml — gates, roster, and budgets, fixed at setup.

The loops read this; they never invent it.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

GATE_TYPES = frozenset({"command", "judge"})
ROLES = ("worker", "judge", "planner")

CONFIG_NAME = "giro.toml"


class ConfigError(Exception):
    """giro.toml is missing or invalid."""


@dataclass
class GateSpec:
    name: str
    type: str  # "command" | "judge"
    run: str = ""  # command gates
    rubric: str = ""  # judge gates — inline criterion text
    criterion: str = ""  # judge gates — name resolving to prompts/<name>.md


@dataclass
class RosterEntry:
    driver: str = "claude"
    model: str = ""
    args: list[str] = field(default_factory=list)


@dataclass
class ProjectionSettings:
    """``[github_projection]`` — GitHub as a one-way display of the store (ADR-0014).

    Off unless enabled, because it is never load-bearing: with ``enabled``
    false the engine makes no GitHub call at all. When ``enabled`` is true,
    the Run's preflight (``gh`` present, token valid, repo reachable, labels
    creatable) is fatal on failure — the operator asked for the surface, so
    "silently off" would be the wrong answer. Only *runtime* GitHub failures
    fail soft (ADR-0014): a rate limit or a transient network glitch mid-Run
    logs and moves on, so a Run never dies for lack of visibility.
    """

    enabled: bool = False
    repo: str = ""  # "owner/name"; empty = autodetected from the origin remote
    timeout: int = 30  # seconds per GitHub call — a `gh` invocation or a checkpoint's push
    # The human an escalation is assigned to and mentions — the notification
    # that reaches a phone. Empty = nobody is assigned, and needs-human is a
    # label and a comment that notify no one.
    assignee: str = ""


@dataclass
class Config:
    root: Path
    verify_gates: list[GateSpec]
    validate_gates: list[GateSpec]
    roster: dict[str, RosterEntry]
    projection: ProjectionSettings = field(default_factory=ProjectionSettings)
    base_branch: str = ""  # "" = the invoking checkout's branch at first activation
    concurrency: int = 1
    max_workers: int = 4  # ceiling on concurrent worker contexts across this checkout's Runs
    issue_attempts: int = 3
    validate_cycles: int = 3
    gate_timeout: int = 600
    context_timeout: int = 3600

    def roster_for(self, role: str) -> RosterEntry:
        """Resolve a role, falling back to the worker's driver and model.

        The fallback deliberately drops the worker's ``args``: permission
        and sandbox-bypass flags must be granted per role, in visible config,
        never inherited silently by a judge or planner that only needs to read.
        """
        if role in self.roster:
            return self.roster[role]
        base = self.roster.get("worker")
        if base is not None:
            return RosterEntry(driver=base.driver, model=base.model)
        return RosterEntry()


def _parse_gates(section: object, where: str) -> list[GateSpec]:
    if section is None:
        return []
    if not isinstance(section, dict) or not isinstance(section.get("gates"), list):
        raise ConfigError(f"[{where}] must contain a 'gates' array")
    gates: list[GateSpec] = []
    for i, raw in enumerate(section["gates"]):
        if not isinstance(raw, dict):
            raise ConfigError(f"[{where}] gate #{i + 1} must be a table")
        name = raw.get("name") or f"{where}-{i + 1}"
        gtype = raw.get("type", "")
        if gtype == "prompt":
            raise ConfigError(
                f"[{where}] gate {name!r} type 'prompt' is retired; change to "
                "type='judge' and move 'rubric' into a judge gate"
            )
        if gtype == "skill":
            raise ConfigError(
                f"[{where}] gate {name!r} type 'skill' is retired; change to "
                "type='judge' with criterion='<name>' — the criterion file now "
                "lives at prompts/<name>.md, not gates/<name>/SKILL.md"
            )
        if gtype not in GATE_TYPES:
            raise ConfigError(
                f"[{where}] gate {name!r}: type must be one of {sorted(GATE_TYPES)}"
            )
        gate = GateSpec(
            name=str(name),
            type=gtype,
            run=str(raw.get("run", "")),
            rubric=str(raw.get("rubric", "")),
            criterion=str(raw.get("criterion", "")),
        )
        if gtype == "command":
            if not gate.run:
                raise ConfigError(f"[{where}] gate {name!r}: command gates require 'run'")
        else:  # gtype == "judge"
            if gate.rubric and gate.criterion:
                raise ConfigError(
                    f"[{where}] gate {name!r}: specify one of 'rubric' or 'criterion', not both"
                )
            if not gate.rubric and not gate.criterion:
                raise ConfigError(
                    f"[{where}] gate {name!r}: judge gate requires 'criterion' or 'rubric'"
                )
        gates.append(gate)
    return gates


def _parse_roster(raw: object) -> dict[str, RosterEntry]:
    roster: dict[str, RosterEntry] = {}
    if raw is None:
        return roster
    if not isinstance(raw, dict):
        raise ConfigError("[runner.roster] must be a table of role entries")
    warned_implementer = False
    has_worker = "worker" in raw
    for role, entry in raw.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"[runner.roster] {role!r} must be a table")
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ConfigError(f"[runner.roster] {role!r}: 'args' must be a list of strings")
        target_role = role
        if role == "implementer":
            if not warned_implementer:
                print(
                    "giro.toml: [runner.roster] role 'implementer' is renamed to 'worker'; "
                    "rename it — the legacy name is accepted for now",
                    file=sys.stderr,
                )
                warned_implementer = True
            if has_worker:
                # Both set: 'worker' wins, the legacy entry is ignored.
                continue
            target_role = "worker"
        roster[target_role] = RosterEntry(
            driver=str(entry.get("driver", "claude")),
            model=str(entry.get("model", "")),
            args=list(args),
        )
    return roster


def _positive_int(section: dict, key: str, default: int) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{key!r} must be a positive integer")
    return value


def _parse_github_projection(raw: object) -> ProjectionSettings:
    if raw is None:
        return ProjectionSettings()
    if not isinstance(raw, dict):
        raise ConfigError("[github_projection] must be a table")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("[github_projection] 'enabled' must be true or false")
    repo = raw.get("repo", "")
    if not isinstance(repo, str):
        raise ConfigError("[github_projection] 'repo' must be a string")
    if repo and not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
        raise ConfigError(
            f"[github_projection] 'repo' must be \"owner/name\", not {repo!r} — leave it "
            "unset to autodetect it from the origin remote"
        )
    assignee = raw.get("assignee", "")
    if not isinstance(assignee, str):
        raise ConfigError("[github_projection] 'assignee' must be a GitHub login")
    return ProjectionSettings(
        enabled=enabled,
        repo=repo,
        timeout=_positive_int(raw, "timeout", 30),
        assignee=assignee.strip().lstrip("@"),  # written either way, used one way
    )


KNOWN_TOP_LEVEL = frozenset({"verify", "validate", "runner", "budget", "github_projection"})
KNOWN_RUNNER = frozenset({"roster", "concurrency", "max_workers", "base_branch"})
KNOWN_ROSTER_ROLES = frozenset({"worker", "judge", "planner"})


MIGRATED_KEYS = {"top level": frozenset({"projection"})}


def _warn_unknown(container: dict, known: frozenset[str], where: str) -> None:
    """A stray key here is a typo (``concurrancy``) or a stale name
    (``reviewer``) — say so on stderr, keep parsing. Silent acceptance would
    turn every future rename into a mystery bug."""
    migrated = MIGRATED_KEYS.get(where, frozenset())
    for key in container:
        if key in known or key in migrated:
            continue
        # 'implementer' has its own deprecation notice in _parse_roster.
        if where == "[runner.roster]" and key == "implementer":
            continue
        print(
            f"giro.toml: unknown key {key!r} in {where} — ignored "
            f"(known: {', '.join(sorted(known))})",
            file=sys.stderr,
        )


def load_config(root: Path) -> Config:
    path = root / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{CONFIG_NAME} not found in {root} — run `giro init` first")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_NAME} is not valid TOML: {exc}") from exc

    runner = data.get("runner", {})
    budget = data.get("budget", {})
    if not isinstance(runner, dict) or not isinstance(budget, dict):
        raise ConfigError("[runner] and [budget] must be tables")

    _warn_unknown(data, KNOWN_TOP_LEVEL, "top level")
    _warn_unknown(runner, KNOWN_RUNNER, "[runner]")
    roster_raw = runner.get("roster")
    if isinstance(roster_raw, dict):
        _warn_unknown(roster_raw, KNOWN_ROSTER_ROLES, "[runner.roster]")

    verify_gates = _parse_gates(data.get("verify"), "verify")
    if not verify_gates:
        raise ConfigError("[verify] must define at least one gate")
    validate_gates = _parse_gates(data.get("validate"), "validate")
    if not validate_gates:
        raise ConfigError(
            "[validate] must define at least one gate — otherwise every Spec "
            "would validate silently (all([]) is True, so the Spec-level gate "
            "set would pass without judging anything)"
        )

    base_branch = runner.get("base_branch", "")
    if not isinstance(base_branch, str):
        raise ConfigError("[runner] 'base_branch' must be a string")

    if "projection" in data:
        raise ConfigError(
            "[projection] was renamed to [github_projection]. The old 'strict' key "
            "is gone: preflight is always fatal when [github_projection] enabled = true, "
            "and only runtime GitHub failures fail soft (ADR-0014). Rename the section "
            "and remove 'strict' if it is set."
        )

    return Config(
        root=root,
        verify_gates=verify_gates,
        validate_gates=validate_gates,
        roster=_parse_roster(runner.get("roster")),
        projection=_parse_github_projection(data.get("github_projection")),
        base_branch=base_branch,
        concurrency=_positive_int(runner, "concurrency", 1),
        max_workers=_positive_int(runner, "max_workers", 4),
        issue_attempts=_positive_int(budget, "issue_attempts", 3),
        validate_cycles=_positive_int(budget, "validate_cycles", 3),
        gate_timeout=_positive_int(budget, "gate_timeout", 600),
        context_timeout=_positive_int(budget, "context_timeout", 3600),
    )


INIT_TEMPLATE = """\
# giro configuration — gates, roster, budgets. Reference: "Configuration" in docs/design.md
# on https://github.com/pierg/giro.

[verify]  # Issue-level gate set: runs after every worker attempt
gates = [
  # default judged gate: issue-fit without scope creep, code quality, honest tests
  { name = "review", type = "judge", criterion = "review" },
  # command gates: exit 0 = pass. Add your project's test command; the giro-setup
  # skill fills this in for you. Examples:
  #   { name = "test", type = "command", run = "pytest -q" },
  #   { name = "test", type = "command", run = "npm test" },
  #   { name = "test", type = "command", run = "cargo test" },
  # enable once CONTEXT.md / docs/adr exist (the `grill` skill creates them):
  # { name = "conformance", type = "judge", criterion = "conformance" },
]

[validate]  # Spec-level gate set: runs once every child Issue is terminal
gates = [
  { name = "spec-fit", type = "judge", rubric = "Every acceptance criterion in the Spec is met." },
]

[runner]
concurrency = 1  # 1 = sequential fresh workers; >1 = isolated worktrees, merged serially
max_workers = 4  # ceiling on concurrent worker contexts across this checkout's Runs — a Run
                 # dispatched past the ceiling waits for a slot rather than exceeding it
# base_branch = "main"  # what giro/<slug> forks from; unset = the branch you dispatch
                        # from, resolved once and recorded in Spec frontmatter

[runner.roster]  # drivers: claude | agy | codex | gemini; args pass through to the CLI.
# The worker edits files, so it needs its agent CLI's permission-bypass flag;
# keep that flag OUT of judge/planner — they only read. Per driver, the flag is:
#   claude  --dangerously-skip-permissions
#   agy     --dangerously-skip-permissions
#   codex   --dangerously-bypass-approvals-and-sandbox
#   gemini  --yolo
# Workers run arbitrary code with whatever you grant here — see "Running safely" in
# docs/guide.md on https://github.com/pierg/giro.
worker = { driver = "claude", args = ["--dangerously-skip-permissions"] }
judge = { driver = "claude" }       # blind, read-only — no bypass flag
planner = { driver = "claude" }     # reads the Spec, returns a plan — no bypass flag

[github_projection]  # giro's state rendered onto GitHub, one-way (ADR-0014)
enabled = false  # off unless you turn it on; when true, preflight is fatal on failure —
                 # `giro project --check` runs the same preflight so you can verify first.
                 # Runtime GitHub failures (rate limit, transient network) still fail soft
                 # so a Run never dies for lack of visibility.
# repo = "owner/name" # unset = autodetected from the origin remote
# assignee = "your-github-login"  # who needs-human is assigned to and mentions; unset =
                                  # the escalation is labelled and commented, and pings nobody
# timeout = 30        # seconds per GitHub call — a `gh` one, or a checkpoint's push
# Auth: GITHUB_TOKEN in the project's git-ignored .env (the engine passes it to `gh`
# through the subprocess environment; `gh`'s own login is the fallback). Recommended: a
# fine-grained token scoped to this one repository — issues, pull requests, contents,
# commit statuses, read/write — owned by a machine account, so mentions and assignments
# actually notify you: GitHub never notifies anyone about their own actions, so a token
# that IS the assignee renders every escalation silently. `giro project --check` says
# which identity is in play.

[budget]  # the "never silent" bounds
issue_attempts = 3     # verify retries per Issue before needs-human
validate_cycles = 3    # gap -> re-wave loops before needs-human
gate_timeout = 600     # seconds per command gate
context_timeout = 3600 # seconds per spawned LLM context before it is killed
"""
