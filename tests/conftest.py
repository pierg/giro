"""Shared fixtures: a real git repo with a Spec, driven by fake contexts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from giro.config import load_config
from giro.loops import Engine
from giro.store import Store
from giro.workspace import Workspace

MINIMAL_TOML = """\
[verify]
gates = [
  { name = "feature", type = "command", run = "test -f feature.txt" },
]

[validate]
gates = [
  { name = "spec-fit", type = "judge", rubric = "Everything the spec asks for exists." },
]

[runner]
concurrency = 1

[budget]
issue_attempts = 3
validate_cycles = 2
gate_timeout = 60
"""


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A clean git repo with giro.toml and one spec with one ready issue."""
    root = tmp_path / "target"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "giro-test")

    (root / "giro.toml").write_text(MINIMAL_TOML)
    spec_dir = root / "docs" / "specs" / "demo"
    (spec_dir / "issues").mkdir(parents=True)
    (spec_dir / "SPEC.md").write_text(
        "---\nstate: draft\n---\n# Demo feature\n\nThe repo contains feature.txt saying ok.\n"
    )
    (spec_dir / "issues" / "01-write-feature.md").write_text(
        "---\nstate: ready\nblocked_by: []\nattempts: 0\n---\n"
        "# Write feature file\n\nCreate feature.txt containing ok.\n"
    )
    (root / "README.md").write_text("# target\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "seed")
    return root


def spec_worktree(root: Path, slug: str = "demo") -> Path:
    """The engine's persistent worktree for a Spec — where its loop actually runs."""
    return Workspace(root).spec_worktree_path(slug)


def spec_store(root: Path, slug: str = "demo") -> Store:
    """The Spec's truth after a Run: the copy on ``giro/<slug>``, not the checkout's."""
    return Store(spec_worktree(root, slug))


def make_engine(root: Path, *, worker, judge=None, planner=None, projection=None) -> Engine:
    cfg = load_config(root)
    drivers = {
        "worker": worker,
        "judge": judge if judge is not None else worker,
        "planner": planner if planner is not None else worker,
    }
    engine = Engine(cfg=cfg, store=Store(root), workspace=Workspace(root), drivers=drivers)
    if projection is not None:  # default: Projection off, as the engine ships
        engine.projection = projection
    return engine


def good_worker(prompt: str, cwd: Path) -> dict:
    (cwd / "feature.txt").write_text("ok\n")
    return {"outcome": "completed", "summary": "wrote feature.txt"}


def lazy_worker(prompt: str, cwd: Path) -> dict:
    return {"outcome": "completed", "summary": "claims done, did nothing"}
