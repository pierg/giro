"""Prompt resolution — where each LLM touchpoint's text comes from.

giro's Markdown falls in two directories, each consumed a different way:

- ``prompts/`` — **engine-injected plain markdown** the engine splices into a
  spawned context (``worker`` discipline, ``planner`` slicing judgment, and
  the ``review`` / ``conformance`` judge-gate criteria). These are plain
  ``<name>.md`` files with no YAML frontmatter — they are text, not host
  discovery artefacts, so nothing here is invokable from a chat host.
- ``skills/`` — **host-invoked operator skills** a chat host discovers and a
  person invokes. Two kinds live here side by side: the engine skills that
  drive giro (the ``giro`` doorway and the ``giro-setup`` configuration skill),
  and the standalone authoring skills that need no giro at all (``spec``,
  ``plan``, ``grill`` — they emit plain Specs, Issues, and ADRs the engine
  later ingests). ``giro install`` copies these into ``.claude/skills/`` for
  discovery. Their frontmatter is the host's index, not something the engine
  strips.

Both categories resolve in override order — a project's own copy wins, then
the giro-bundled default. Host skills additionally honor a ``.claude/skills``
placement (where ``giro install`` and other hosts put them); engine prompts
have no ``.claude`` leg, because that directory is host-discovery territory
and the engine must never surface its internals there.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CATEGORIES = ("prompts", "skills")
_BUNDLED = {"prompts": "_prompts", "skills": "_skills"}
_MANIFEST = ".giro-install.json"


def bundled_dir(category: str) -> Path | None:
    """The files giro ships for ``category``: in the wheel as
    ``giro/_<category>``, in a dev checkout as the repo's ``<category>/``."""
    packaged = Path(__file__).parent / _BUNDLED[category]
    if packaged.is_dir():
        return packaged
    dev = Path(__file__).resolve().parents[2] / category
    return dev if dev.is_dir() else None


def resolve_prompt(root: Path, category: str, name: str) -> str | None:
    """Return the resolved body for ``name`` in ``category``, honoring overrides.

    For ``prompts``, the file is ``<root>/prompts/<name>.md`` — a plain
    markdown file the engine injects verbatim. For ``skills``, the file is
    ``<root>/skills/<name>/SKILL.md`` — a host-discovery skill whose
    frontmatter and body come back untouched, because a skill is read by a
    host, not injected into an engine context.

    Resolution order: project ``<root>/<category>/...``, then (host skills
    only) ``<root>/.claude/skills/<name>/SKILL.md``, then the bundled default.
    """
    if category == "prompts":
        candidates = [root / "prompts" / f"{name}.md"]
        bundled = bundled_dir("prompts")
        if bundled is not None:
            candidates.append(bundled / f"{name}.md")
    else:
        candidates = [root / "skills" / name / "SKILL.md"]
        candidates.append(root / ".claude" / "skills" / name / "SKILL.md")
        bundled = bundled_dir("skills")
        if bundled is not None:
            candidates.append(bundled / name / "SKILL.md")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def skill_digest(skill_dir: Path) -> str:
    """Content hash of one skill directory — every file, path-labelled, so a
    renamed file counts as much as an edited one."""
    h = hashlib.sha256()
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(skill_dir)).encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def read_manifest(dest_dir: Path) -> dict[str, str]:
    """What ``giro install`` placed in ``dest_dir``, as name → content hash."""
    path = dest_dir / _MANIFEST
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    skills = data.get("skills", {})
    return skills if isinstance(skills, dict) else {}


def record_placement(dest_dir: Path, name: str) -> None:
    """Update the install manifest after placing ``name``. The recorded hash is
    what later tells a stale copy (unedited, bundled moved on) from a
    customized one (deliberately edited — an override, not drift)."""
    manifest = read_manifest(dest_dir)
    manifest[name] = skill_digest(dest_dir / name)
    (dest_dir / _MANIFEST).write_text(
        json.dumps({"skills": manifest}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def installed_skill_drift(root: Path) -> list[dict[str, str]]:
    """How ``<root>/.claude/skills`` relates to this giro's bundled skills.

    One ``{"name", "state"}`` entry per non-clean skill:

    - ``missing`` — bundled skill absent from an existing install dir
    - ``stale`` — the copy is exactly what an older install placed; the
      bundled skill has since moved on
    - ``customized`` — the copy was edited after placement: an override by
      design, reported for JSON readers but nothing to fix
    - ``unknown`` — the copy differs and no manifest records what was placed

    An absent ``.claude/skills`` is not drift — the project never opted in.
    """
    dest = root / ".claude" / "skills"
    bundled = bundled_dir("skills")
    if bundled is None or not dest.is_dir():
        return []
    manifest = read_manifest(dest)
    entries: list[dict[str, str]] = []
    for source in sorted(p for p in bundled.iterdir() if (p / "SKILL.md").is_file()):
        installed = dest / source.name
        if not (installed / "SKILL.md").is_file():
            entries.append({"name": source.name, "state": "missing"})
            continue
        placed = skill_digest(installed)
        if placed == skill_digest(source):
            continue
        recorded = manifest.get(source.name)
        if recorded is None:
            state = "unknown"
        elif placed == recorded:
            state = "stale"
        else:
            state = "customized"
        entries.append({"name": source.name, "state": state})
    return entries
