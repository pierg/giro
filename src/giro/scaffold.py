"""The authoring seam — what a fresh Spec, Issue, or ADR looks like on disk.

One home for the two things every authoring path needs: the numbering scheme
(``NN-slug`` Issues, ``NNNN-slug`` ADRs) and the starter body templates. The
engine's ``giro new`` command builds on it, and so do the standalone authoring
skills (``spec``, ``plan``, ``grill``), which produce the same artifacts with
no giro install at all — this module is the shared contract between them.

Deliberately dependency-free: standard library only, no other giro module, so
it can be read by a skill that never imports the package. It writes *bodies*
and allocates *numbers*; it never writes the lifecycle frontmatter (``state``,
``attempts``) — that zone is the engine's alone (ADR-0009). An artifact born
plain, with no frontmatter, reads back as its initial state (``draft`` for a
Spec, ``ready`` for an Issue) until the engine first stamps it at activation.
"""

from __future__ import annotations

import re
from pathlib import Path


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "issue"


# Starter bodies. A skill overwrites these with its full template; they are the
# hint a bare `giro new` leaves behind, and they name the skill that carries the
# real structure — never engine internals, so they read the same with or without
# giro installed.
SPEC_BODY = """\
Problem, solution, user stories, implementation and testing decisions, out of
scope — the body is yours; the `spec` skill carries the template.
"""

ISSUE_BODY = """\
What to build — the end-to-end behaviour this Issue makes work — and a
checklist of acceptance criteria. See the `plan` skill.
"""

ADR_BODY = """\
1-3 sentences: the context, what was decided, and why — one paragraph is a
complete ADR. See the `grill` skill's ADR-FORMAT.
"""


def _next_number(directory: Path) -> int:
    """The next ``N`` for a ``NN-…``/``NNNN-…`` sequence in ``directory``."""
    numbers = [
        int(m.group(1))
        for p in directory.glob("*.md")
        if (m := re.match(r"(\d+)-", p.name))
    ]
    return max(numbers, default=0) + 1


def issue_id(issues_dir: Path, title: str) -> str:
    """The next ``NN-slug`` Issue id under ``issues_dir`` (need not exist yet)."""
    number = _next_number(issues_dir) if issues_dir.is_dir() else 1
    return f"{number:02d}-{slugify(title)}"


def adr_filename(adr_dir: Path, title: str) -> str:
    """The next ``NNNN-slug.md`` ADR filename under ``adr_dir``."""
    number = _next_number(adr_dir) if adr_dir.is_dir() else 1
    return f"{number:04d}-{slugify(title)}.md"


def write_adr(root: Path, title: str) -> Path:
    """Scaffold ``docs/adr/NNNN-slug.md`` with the starter body; return its path.

    Plain markdown, no frontmatter — an ADR carries no engine state, so this is
    the whole artifact whether or not giro is installed.
    """
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    path = adr_dir / adr_filename(adr_dir, title)
    path.write_text(f"# {title}\n\n{ADR_BODY}", encoding="utf-8")
    return path
