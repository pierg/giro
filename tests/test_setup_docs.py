"""The human bootstrap path is install-then-setup, not init-then-install:
`giro install` places host skills, then the `giro-setup` skill (invoked in chat)
configures the project. `giro init` is mentioned only as the non-interactive
fallback. This guards the guide's chat walkthrough (and the README's pointer to it)
and the `giro` doorway skill's
Setup section against drifting back to init-first copy."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cmd_init_next_message():
    """Extract the literal string cmd_init prints as its 'Next:' message.

    Reads it via ast rather than raw text offsets so the test survives
    reformatting (e.g. the closing quote and paren landing on separate
    lines) instead of crashing with ValueError when a literal search for
    adjacent characters comes up empty.
    """
    tree = ast.parse((REPO_ROOT / "src" / "giro" / "cli.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_init":
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "print"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                    and call.args[0].value.startswith("Next:")
                ):
                    return call.args[0].value
    raise AssertionError("cmd_init has no print(...) call whose message starts with 'Next:'")


def test_readme_names_the_setup_skill_as_the_way_in():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert text.index("giro install") < text.index("giro-setup")


def test_guide_orders_install_before_setup_before_spec():
    text = (REPO_ROOT / "docs" / "guide.md").read_text(encoding="utf-8")

    install_idx = text.index("giro install")
    setup_idx = text.index('"set up giro"')
    spec_idx = text.index("the spec skill writes")
    fallback_idx = text.index("`giro init` writes a starter")

    assert install_idx < setup_idx < spec_idx < fallback_idx
    assert "the human path" in text.lower()


def test_giro_doorway_skill_setup_section_names_setup_and_defers_init():
    text = (REPO_ROOT / "skills" / "giro" / "SKILL.md").read_text(encoding="utf-8")

    install_idx = text.index("giro install")
    roster_idx = text.index("giro, giro-setup, spec, plan, grill")
    setup_skill_idx = text.index('"set up giro" invokes the **`giro-setup`** skill')
    fallback_idx = text.index("`giro init` is the non-interactive fallback")

    assert install_idx < roster_idx < setup_skill_idx < fallback_idx


def test_cmd_init_next_copy_points_at_install_and_setup():
    next_copy = _cmd_init_next_message()

    assert "giro install" in next_copy
    assert "`giro-setup`" in next_copy
