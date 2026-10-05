"""Driver tests: argv recipes, JSON extraction, and error surfacing.

The auth-expired incident (M1, 2026-08-06) is pinned here: a CLI failure
must surface the CLI's own message, never a bare exit code.

Prompts travel via stdin, never argv (F5) — the argv limit is 128 KiB and
prompts routinely exceed it. Tests monkeypatch ``subprocess.Popen`` so the
captured argv can be asserted to *not* contain the prompt.
"""

import json
import subprocess
from pathlib import Path

import pytest

from giro import drivers as drivers_mod
from giro.drivers import (
    AgyDriver,
    ClaudeDriver,
    CodexDriver,
    DriverError,
    GeminiDriver,
    build_driver,
    extract_json,
)


def test_extract_json_takes_last_object():
    text = 'I did {"step": 1} things.\n```json\n{"verdict": "pass"}\n```\n'
    assert extract_json(text) == {"verdict": "pass"}


def test_extract_json_none_found():
    with pytest.raises(DriverError):
        extract_json("no json here { broken")


class _FakePopen:
    """A stand-in for subprocess.Popen used by ``_run_bounded``."""

    def __init__(self, argv, *, stdout="", stderr="", returncode=0, boom=None, **kwargs):
        self.args = argv
        self.pid = 12345
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._boom = boom
        self.spawn_kwargs = kwargs

    def communicate(self, input=None, timeout=None):
        if self._boom is not None:
            raise self._boom
        self.stdin_input = input
        return self._stdout, self._stderr


def run_with(monkeypatch, driver, stdout="", stderr="", returncode=0, boom=None):
    """Drive a Driver against a monkeypatched Popen and return (argv, input, result)."""
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakePopen(
            argv, stdout=stdout, stderr=stderr, returncode=returncode, boom=boom, **kwargs
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    proc: dict = {}

    def wrapped_bounded(argv, *, cwd, timeout, input=None, env=None):
        popen = fake_popen(argv, cwd=cwd, timeout=timeout)
        stdout_, stderr_ = popen.communicate(input=input, timeout=timeout)
        captured["input"] = input
        proc["popen"] = popen
        return subprocess.CompletedProcess(argv, popen.returncode, stdout_, stderr_)

    monkeypatch.setattr(drivers_mod, "_run_bounded", wrapped_bounded)
    try:
        result = driver.run("PROMPT", {}, cwd=Path("."), model="m1")
    except DriverError:
        raise
    return captured.get("argv"), captured.get("input"), result


def test_claude_unwraps_result_field(monkeypatch):
    outer = json.dumps({"type": "result", "result": 'done. {"verdict": "pass"}'})
    argv, stdin, result = run_with(monkeypatch, ClaudeDriver(), stdout=outer)
    assert result == {"verdict": "pass"}
    assert argv[:2] == ["claude", "-p"]
    assert "PROMPT" not in argv  # prompt is on stdin, never argv
    assert stdin == "PROMPT"
    assert argv[-2:] == ["--model", "m1"]


def test_claude_surfaces_is_error_message(monkeypatch):
    outer = json.dumps(
        {"is_error": True, "result": "Failed to authenticate: OAuth session expired"}
    )

    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        return subprocess.CompletedProcess(argv, 1, outer, "")

    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    with pytest.raises(DriverError, match="OAuth session expired"):
        ClaudeDriver().run("PROMPT", {}, cwd=Path("."))


def test_claude_surfaces_is_error_even_on_exit_zero(monkeypatch):
    outer = json.dumps({"is_error": True, "result": "quota exhausted"})

    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        return subprocess.CompletedProcess(argv, 0, outer, "")

    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    with pytest.raises(DriverError, match="quota exhausted"):
        ClaudeDriver().run("PROMPT", {}, cwd=Path("."))


def test_agy_stream_json_recipe(monkeypatch):
    stream = "\n".join(
        [
            json.dumps({"event": "init", "init": {"model": "m1"}}),
            json.dumps(
                {"event": "result", "result": {"status": "SUCCESS", "response": '{"ok": true}'}}
            ),
        ]
    )
    argv, stdin, result = run_with(monkeypatch, AgyDriver(timeout=60), stdout=stream)
    assert result == {"ok": True}
    assert argv[0] == "agy"
    assert argv[argv.index("--input-format") :][:2] == ["--input-format", "stream-json"]
    assert argv[argv.index("--output-format") :][:2] == ["--output-format", "stream-json"]
    assert argv[argv.index("--print-timeout") :][:2] == ["--print-timeout", "60s"]
    assert argv[-2:] == ["--model", "m1"]
    # the prompt rides on stdin as a stream-json user message, never on argv
    assert not any("PROMPT" in a for a in argv)
    assert json.loads(stdin) == {
        "event": "user",
        "message": {"role": "user", "content": "PROMPT"},
    }
    # permission bypass is never hardcoded — it is granted per role, in config
    assert "--dangerously-skip-permissions" not in argv


def test_agy_surfaces_error_status(monkeypatch):
    stream = json.dumps(
        {
            "event": "result",
            "result": {"status": "ERROR", "response": "", "error": "quota exhausted"},
        }
    )

    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        return subprocess.CompletedProcess(argv, 0, stream, "")

    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    with pytest.raises(DriverError, match="quota exhausted"):
        AgyDriver().run("PROMPT", {}, cwd=Path("."))


def test_codex_prompt_is_dash_and_stdin(monkeypatch):
    argv, stdin, _ = run_with(monkeypatch, CodexDriver(), stdout='{"ok": true}')
    assert argv[:2] == ["codex", "exec"]
    assert argv[-1] == "-"  # tells codex to read prompt from stdin
    assert "PROMPT" not in argv
    assert stdin == "PROMPT"
    assert "--skip-git-repo-check" in argv  # structural, not a permission bypass
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_gemini_argv_recipe(monkeypatch):
    argv, stdin, _ = run_with(monkeypatch, GeminiDriver(), stdout='{"ok": true}')
    assert argv[:2] == ["gemini", "-p"]
    assert "PROMPT" not in argv
    assert stdin == "PROMPT"
    assert "--yolo" not in argv  # dangerous auto-approve comes from config args only


def test_permission_flags_come_from_config_args(monkeypatch):
    """The dangerous flags are visible per-role config, so a judge can be spawned
    without them while a worker opts in."""
    argv, _, _ = run_with(
        monkeypatch, GeminiDriver(args=["--yolo"]), stdout='{"ok": true}'
    )
    assert "--yolo" in argv


def test_a_huge_prompt_does_not_appear_on_argv(monkeypatch):
    """F5 — argv has a 128 KiB OS limit; a 200 KiB prompt must not go on it."""
    big = "x" * 200_000
    captured: dict = {}

    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        captured["argv"] = argv
        captured["input"] = input
        return subprocess.CompletedProcess(argv, 0, '{"ok": true}', "")

    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    ClaudeDriver().run(big, {}, cwd=Path("."))
    assert big not in captured["argv"]
    assert captured["input"] == big  # travels on stdin


def test_run_bounded_uses_start_new_session(monkeypatch):
    """F6 — every bounded subprocess spawns in its own session so a timeout can
    kill the whole process group (orphans included)."""
    captured: dict = {}

    class Spy:
        def __init__(self, argv, **kwargs):
            captured["kwargs"] = kwargs
            self.pid = 1
            self.returncode = 0

        def communicate(self, input=None, timeout=None):
            return '{"ok": true}', ""

    monkeypatch.setattr(subprocess, "Popen", Spy)
    ClaudeDriver().run("PROMPT", {}, cwd=Path("."))
    assert captured["kwargs"].get("start_new_session") is True


def test_build_driver_threads_timeout():
    driver = build_driver("agy", timeout=123)
    assert driver.timeout == 123


def test_stderr_surfaced_on_failure(monkeypatch):
    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        return subprocess.CompletedProcess(argv, 41, "", "billing disabled")

    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    with pytest.raises(DriverError, match="billing disabled"):
        AgyDriver().run("PROMPT", {}, cwd=Path("."))


def test_registry_and_unknown_driver():
    assert isinstance(build_driver("codex"), CodexDriver)
    with pytest.raises(DriverError, match="unknown driver"):
        build_driver("hal9000")


def test_extra_args_pass_through(monkeypatch):
    driver = ClaudeDriver(args=["--dangerously-skip-permissions"])
    outer = json.dumps({"result": '{"ok": true}'})
    argv, _, _ = run_with(monkeypatch, driver, stdout=outer)
    assert "--dangerously-skip-permissions" in argv


def test_recipe_driver_stdin(monkeypatch):
    from giro.drivers import RecipeDriver
    driver = RecipeDriver(
        command=["cursor-agent", "-p"],
        prompt_delivery="stdin",
        model_template="--model {model}",
        unwrap="result",
        error_when="is_error"
    )
    outer = json.dumps({"result": '{"ok": true}'})
    argv, stdin, result = run_with(monkeypatch, driver, stdout=outer)
    assert result == {"ok": True}
    assert argv == ["cursor-agent", "-p", "--model", "m1"]
    assert stdin == "PROMPT"

def test_recipe_driver_positional_dash(monkeypatch):
    from giro.drivers import RecipeDriver
    driver = RecipeDriver(
        command=["codex", "exec"],
        prompt_delivery="positional-dash",
        model_template="",
    )
    argv, stdin, result = run_with(monkeypatch, driver, stdout='{"ok": true}')
    assert result == {"ok": True}
    assert argv == ["codex", "exec", "-"]
    assert stdin == "PROMPT"

def test_recipe_driver_file(monkeypatch):
    from giro.drivers import RecipeDriver
    driver = RecipeDriver(
        command=["gemini", "-p"],
        prompt_delivery="file",
        model_template="",
    )
    # prompt is in file, passed as positional arg
    captured = {}
    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        captured["argv"] = argv
        captured["input"] = input
        # read the file
        captured["file_content"] = Path(argv[-1]).read_text()
        return subprocess.CompletedProcess(argv, 0, '{"ok": true}', "")
    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    result = driver.run("PROMPT", {}, cwd=Path("."))
    assert result == {"ok": True}
    assert captured["argv"][:-1] == ["gemini", "-p"]
    assert captured["input"] is None
    assert captured["file_content"] == "PROMPT"
    assert not Path(captured["argv"][-1]).exists() # file is removed

def test_recipe_driver_error_when(monkeypatch):
    from giro.drivers import RecipeDriver
    driver = RecipeDriver(
        command=["cursor-agent"],
        prompt_delivery="stdin",
        model_template="",
        unwrap="result",
        error_when="is_error"
    )
    # The error message should be from the unwrap key if it's there
    outer = json.dumps({"is_error": True, "result": "Quota exceeded"})
    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        return subprocess.CompletedProcess(argv, 0, outer, "")
    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    with pytest.raises(DriverError, match="Quota exceeded"):
        driver.run("PROMPT", {}, cwd=Path("."))

def test_recipe_driver_error_when_string_payload(monkeypatch):
    from giro.drivers import RecipeDriver
    driver = RecipeDriver(
        command=["cursor-agent"],
        prompt_delivery="stdin",
        model_template="",
        error_when="error"
    )
    # If error_when has a string, it should use that string as message
    outer = json.dumps({"error": "Quota exceeded"})
    def fake_bounded(argv, *, cwd, timeout, input=None, env=None):
        return subprocess.CompletedProcess(argv, 0, outer, "")
    monkeypatch.setattr(drivers_mod, "_run_bounded", fake_bounded)
    with pytest.raises(DriverError, match="Quota exceeded"):
        driver.run("PROMPT", {}, cwd=Path("."))
