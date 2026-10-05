"""`giro init` is the non-interactive fallback: it still writes a loadable config,
and its stdout points humans at `giro install` + the `giro-setup` skill, not itself."""

from pathlib import Path

from giro.cli import main
from giro.config import load_config
from tests.conftest import git


def test_init_writes_loadable_config_and_points_to_install_and_setup(tmp_path: Path, capsys):
    root = tmp_path / "target"
    root.mkdir()
    git(root, "init", "-b", "main")

    assert main(["-C", str(root), "init"]) == 0
    assert (root / "giro.toml").is_file()
    assert (root / "docs" / "specs").is_dir()

    load_config(root)  # loadable, not just present

    out = capsys.readouterr().out
    assert "giro install" in out
    assert "giro-setup" in out
