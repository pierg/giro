"""The instruction files and the skill projection for this checkout.

giro does not vendor ``kit/``. ``giro install`` still copies host skills into
``.claude/skills`` and hashes those copies. ``.agents/skills`` is a relative
symlink to that directory, so Codex and Cursor read the same files.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 24 * 1024


def test_instruction_files():
    agents = ROOT / "AGENTS.md"
    assert agents.is_file() and not agents.is_symlink()
    assert 0 < agents.stat().st_size <= LIMIT
    assert (ROOT / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n"


def test_skill_projection_is_one_body():
    claude = ROOT / ".claude" / "skills"
    agents = ROOT / ".agents" / "skills"
    assert claude.is_dir() and not claude.is_symlink()
    assert agents.is_symlink() and os.readlink(agents) == "../.claude/skills"
    assert not (ROOT / ".cursor" / "skills").exists()
    names = sorted(p.name for p in claude.iterdir() if (p / "SKILL.md").is_file())
    assert names
    for name in names:
        assert (agents / name / "SKILL.md").resolve() == (claude / name / "SKILL.md").resolve()
