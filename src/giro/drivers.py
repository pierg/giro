"""Drivers — how the engine spawns an LLM context and gets an envelope back.

A Driver's whole contract is: run this prompt in a fresh context at this
cwd, and return the parsed JSON envelope the context ended with. Everything
else (what the prompt says, what the schema demands, what happens to the
result) belongs to the engine.

Four agent CLIs ship as drivers — ``claude``, ``agy``, ``codex``,
``gemini`` — each a thin argv recipe over one shared subprocess runner.
Errors surface the CLI's own message (auth failures, quota limits), never a
bare exit code.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class DriverError(Exception):
    """The context could not be run or produced no parseable JSON."""


class Driver(Protocol):
    def run(
        self, prompt: str, schema: dict[str, Any], cwd: Path, model: str = ""
    ) -> dict[str, Any]: ...


def extract_json(text: str) -> dict[str, Any]:
    """Extract the last complete JSON object from free text (fenced or bare)."""
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    idx = 0
    while (start := text.find("{", idx)) != -1:
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(obj, dict):
            found = obj
        idx = start + end
    if found is None:
        raise DriverError("no JSON object found in context output")
    return found


def tail(text: str, limit: int = 2000) -> str:
    """Keep the last ``limit`` characters — the tail is where the error is."""
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


FakeResponse = dict[str, Any] | Exception | Callable[[str, Path], dict[str, Any]]


class FakeDriver:
    """Scripted driver for tests: canned envelopes, optional side effects.

    Each queued response may be a dict (returned as-is), an Exception
    (raised), or a callable ``(prompt, cwd) -> dict`` for responses that
    need to mutate the workspace the way a real worker would.
    """

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def run(
        self, prompt: str, schema: dict[str, Any], cwd: Path, model: str = ""
    ) -> dict[str, Any]:
        with self._lock:
            self.calls.append({"prompt": prompt, "schema": schema, "cwd": cwd, "model": model})
            if not self._responses:
                raise DriverError("FakeDriver ran out of scripted responses")
            response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(prompt, cwd)
        return response


def _run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a hard time bound and a process-group kill on
    timeout.

    Prompts pass on stdin (``input``), not argv: prompt bodies routinely run
    into hundreds of kilobytes and the OS argv limit is 128 KiB. On timeout
    the whole process group is signalled, so a CLI that spawned children
    leaves no orphans behind. ``encoding='utf-8', errors='replace'`` prevents
    a garbled byte from a subprocess from raising UnicodeDecodeError inside
    the engine.
    """
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,  # its own process group — kill wipes descendants
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the entire process group — a child that ignored SIGTERM or
        # spawned its own descendants cannot outlive us.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from None
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


class SubprocessDriver:
    """Shared shell-out runner: build argv, spawn, extract the final JSON."""

    name = "subprocess"

    # Whether the CLI reads its prompt from stdin. When False, the driver
    # writes the prompt to a temp file and passes it via ``prompt_file_arg``
    # instead of argv — the argv limit is 128 KiB and prompts routinely
    # exceed that. Subclasses override.
    stdin_prompt: bool = True
    prompt_file_arg: str = ""  # e.g. "--prompt-file" when stdin_prompt is False

    def __init__(self, args: list[str] | None = None, timeout: int = 3600):
        self.args = list(args or [])
        self.timeout = timeout

    def build_argv(self, model: str) -> list[str]:  # pragma: no cover
        """Return the argv WITHOUT the prompt — the prompt travels via stdin
        or a temp file, not argv, so any length is safe."""
        raise NotImplementedError

    def postprocess(self, stdout: str) -> str:
        """Hook: reduce raw stdout to the text that carries the envelope."""
        return stdout

    def stdin_payload(self, prompt: str) -> str:
        """Hook: what to write to the CLI's stdin. Default is the prompt
        verbatim; a driver whose CLI frames stdin (e.g. a JSON protocol)
        overrides this to wrap the prompt."""
        return prompt

    def error_detail(self, proc: subprocess.CompletedProcess[str]) -> str:
        """Hook: the most useful message when the CLI fails."""
        return tail(proc.stderr) or tail(proc.stdout)

    def run(
        self, prompt: str, schema: dict[str, Any], cwd: Path, model: str = ""
    ) -> dict[str, Any]:
        argv = self.build_argv(model)
        tmp: Path | None = None
        try:
            if self.stdin_prompt:
                stdin_data: str | None = self.stdin_payload(prompt)
            else:
                # Some CLIs cannot read a prompt from stdin — hand it via a
                # temp file. NamedTemporaryFile with delete=False so the CLI
                # can open it before we clean up.
                fd, name = tempfile.mkstemp(prefix="giro-prompt-", suffix=".txt")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fp:
                        fp.write(prompt)
                except Exception:
                    Path(name).unlink(missing_ok=True)
                    raise
                tmp = Path(name)
                if self.prompt_file_arg:
                    argv = [*argv, self.prompt_file_arg, str(tmp)]
                else:
                    argv = [*argv, str(tmp)]
                stdin_data = None
            try:
                proc = _run_bounded(
                    argv, cwd=cwd, timeout=self.timeout, input=stdin_data
                )
            except FileNotFoundError as exc:
                raise DriverError(f"`{argv[0]}` CLI not found on PATH") from exc
            except subprocess.TimeoutExpired as exc:
                raise DriverError(
                    f"{self.name} context timed out after {self.timeout}s"
                ) from exc
            except OSError as exc:
                raise DriverError(
                    f"{self.name} could not be spawned ({exc})"
                ) from exc
            if proc.returncode != 0:
                raise DriverError(
                    f"{self.name} exited {proc.returncode}: {self.error_detail(proc)}"
                )
            return extract_json(self.postprocess(proc.stdout))
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)



class RecipeDriver(SubprocessDriver):
    """A driver configured entirely from a declarative recipe, supporting
    stdin, positional-dash, and file prompt delivery, model-flag templates,
    and envelope unwrapping."""

    name = "recipe"

    def __init__(
        self,
        command: list[str],
        prompt_delivery: str,
        model_template: str,
        unwrap: str | None = None,
        error_when: str | None = None,
        args: list[str] | None = None,
        timeout: int = 3600,
    ):
        super().__init__(args=args, timeout=timeout)
        self.command = list(command)
        self.prompt_delivery = prompt_delivery
        self.model_template = model_template
        self.unwrap = unwrap
        self.error_when = error_when
        self.stdin_prompt = (self.prompt_delivery != "file")

    def build_argv(self, model: str) -> list[str]:
        argv = [*self.command, *self.args]
        if model and self.model_template:
            argv.extend(self.model_template.replace("{model}", model).split())
        if self.prompt_delivery == "positional-dash":
            argv.append("-")
        return argv

    def _outer(self, stdout: str) -> dict[str, Any] | None:
        try:
            return extract_json(stdout)
        except DriverError:
            return None

    def postprocess(self, stdout: str) -> str:
        if not self.unwrap and not self.error_when:
            return stdout

        outer = self._outer(stdout)
        if outer is None:
            return stdout

        if self.error_when and outer.get(self.error_when):
            payload = outer[self.error_when]
            if isinstance(payload, str) and payload != "":
                msg = payload
            elif self.unwrap and outer.get(self.unwrap):
                msg = str(outer[self.unwrap])
            else:
                msg = str(payload)
            raise DriverError(f"{self.name}: {msg}")

        if self.unwrap:
            result = outer.get(self.unwrap)
            return result if isinstance(result, str) else stdout

        return stdout

    def error_detail(self, proc: subprocess.CompletedProcess[str]) -> str:
        if not self.unwrap and not self.error_when:
            return super().error_detail(proc)
        outer = self._outer(proc.stdout)
        if outer is not None:
            if self.error_when and outer.get(self.error_when):
                payload = outer[self.error_when]
                if isinstance(payload, str) and payload != "":
                    return payload
            if self.unwrap and outer.get(self.unwrap):
                return str(outer[self.unwrap])
        return super().error_detail(proc)


class ClaudeDriver(SubprocessDriver):
    """``claude -p`` — non-interactive Claude Code context.

    Prompt on stdin: ``claude -p`` with no positional prompt reads from stdin,
    so any prompt length is safe (the argv limit is 128 KiB)."""

    name = "claude"
    stdin_prompt = True

    def build_argv(self, model: str) -> list[str]:
        argv = ["claude", "-p", "--output-format", "json", *self.args]
        if model:
            argv += ["--model", model]
        return argv

    def _outer(self, stdout: str) -> dict[str, Any] | None:
        try:
            outer = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        return outer if isinstance(outer, dict) else None

    def postprocess(self, stdout: str) -> str:
        outer = self._outer(stdout)
        if outer is None:
            return stdout
        # claude reports its own failures (auth, quota) inside the result JSON
        # with is_error=true — surface the message, don't parse it as work.
        if outer.get("is_error"):
            raise DriverError(f"claude: {outer.get('result', 'unknown error')}")
        result = outer.get("result")
        return result if isinstance(result, str) else stdout

    def error_detail(self, proc: subprocess.CompletedProcess[str]) -> str:
        outer = self._outer(proc.stdout)
        if outer is not None and outer.get("result"):
            return str(outer["result"])
        return super().error_detail(proc)


class AgyDriver(SubprocessDriver):
    """Antigravity's ``agy`` CLI, driven through its ``stream-json`` protocol.

    agy's text print mode reads the prompt from ``-p``'s argv *value*, which
    the 128 KiB argv limit rules out for real worker packets. Its stream-json
    mode keeps the prompt on stdin: one NDJSON ``user`` message in
    (``{"event":"user","message":{"role":"user","content":…}}``), a stream of
    NDJSON events out, terminating in a ``result`` event whose ``response``
    carries the reply text (or an ``error`` when ``status`` is not
    ``SUCCESS``).

    Model must be an exact roster name, e.g. ``Gemini 3.1 Pro (High)`` (see
    ``agy models``). Grant write access per role in config with
    ``args = ["--dangerously-skip-permissions"]``."""

    name = "agy"
    stdin_prompt = True

    def build_argv(self, model: str) -> list[str]:
        argv = [
            "agy",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--print-timeout", f"{self.timeout}s",
            *self.args,
        ]
        if model:
            argv += ["--model", model]
        return argv

    def stdin_payload(self, prompt: str) -> str:
        # agy reads one NDJSON message per line from stdin; a `user` event
        # carries the prompt in message.content, keeping it off argv.
        msg = {"event": "user", "message": {"role": "user", "content": prompt}}
        return json.dumps(msg) + "\n"

    def _result(self, stdout: str) -> dict[str, Any] | None:
        """The last ``result`` event's payload from the NDJSON stream, if any."""
        result: dict[str, Any] | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("event") == "result":
                payload = obj.get("result")
                if isinstance(payload, dict):
                    result = payload
        return result

    def postprocess(self, stdout: str) -> str:
        # agy can exit 0 with a failed run reported inside the result event —
        # surface its message, don't parse an empty response as work.
        result = self._result(stdout)
        if result is None:
            return stdout  # no result event: let extract_json fail loudly
        if result.get("status") != "SUCCESS":
            raise DriverError(f"agy: {result.get('error') or 'run failed'}")
        response = result.get("response")
        return response if isinstance(response, str) else stdout

    def error_detail(self, proc: subprocess.CompletedProcess[str]) -> str:
        result = self._result(proc.stdout)
        if result is not None and result.get("error"):
            return str(result["error"])
        return super().error_detail(proc)


class CodexDriver(SubprocessDriver):
    """``codex exec`` — the prompt is read from stdin when we pass ``-`` as
    the positional prompt. Grant write access per role in config with
    ``args = ["--dangerously-bypass-approvals-and-sandbox"]``."""

    name = "codex"
    stdin_prompt = True

    def build_argv(self, model: str) -> list[str]:
        argv = ["codex", "exec", "--skip-git-repo-check", "--color", "never", *self.args]
        if model:
            argv += ["--model", model]
        # `-` as the positional prompt tells codex to read from stdin.
        return [*argv, "-"]


class GeminiDriver(SubprocessDriver):
    """``gemini -p`` — needs GEMINI_API_KEY or prior CLI auth. Grant write
    access per role in config with ``args = ["--yolo"]``.

    The current ``gemini`` CLI requires the prompt as a positional argument
    to ``-p``, so this driver writes the prompt to a temp file and passes it
    via ``--prompt-file`` where supported, or falls back to reading stdin
    where ``-p -`` is honored. As a safest common form the prompt goes on
    stdin — modern versions of the CLI accept it with no positional prompt.
    """

    name = "gemini"
    stdin_prompt = True

    def build_argv(self, model: str) -> list[str]:
        argv = ["gemini", "-p", "--skip-trust", *self.args]
        if model:
            argv += ["--model", model]
        return argv


DRIVER_REGISTRY: dict[str, type[SubprocessDriver]] = {
    "claude": ClaudeDriver,
    "agy": AgyDriver,
    "codex": CodexDriver,
    "gemini": GeminiDriver,
}


def build_driver(
    name: str, args: list[str] | None = None, timeout: int = 3600
) -> Driver:
    if name not in DRIVER_REGISTRY:
        raise DriverError(
            f"unknown driver {name!r}; available: {sorted(DRIVER_REGISTRY)}"
        )
    return DRIVER_REGISTRY[name](args=args, timeout=timeout)
