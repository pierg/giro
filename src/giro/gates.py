"""The gate runner — one contract, two kinds.

A gate is anything that ends in ``{verdict, findings[]}``. Command gates
decide by exit code. Judge gates are judged by a fresh, blind context that
must emit the verdict envelope; a malformed envelope gets one retry and then
fails closed. A judge gate carries either an inline ``rubric`` or a named
``criterion`` that resolves to ``prompts/<name>.md``. Gates run to completion
— every gate in the set runs and every finding is collected, so the next
attempt sees the full picture.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config, GateSpec
from .drivers import Driver, DriverError, tail
from .envelope import (
    VERDICT_SCHEMA,
    EnvelopeError,
    Finding,
    GateVerdict,
    parse_verdict,
)
from .prompts import resolve_prompt

JUDGE_PREAMBLE = """\
You are a verification judge. You did not write this change and you have no
memory of previous attempts. Judge the change in front of you strictly against
the criterion below; you may read the repository for context (the criterion may
ask you to), but judge only the change — do not fix anything and do not run
destructive commands.

Your ENTIRE final output must be a single JSON object matching this schema
(no prose before or after):
{schema}

Return "pass" only if the criterion is met beyond reasonable doubt;
otherwise return "fail" with one finding per distinct problem.
"""


@dataclass
class GateContext:
    """What a gate run may look at."""

    cwd: Path
    material: str  # the diff / artifact under judgment
    judge: Driver
    judge_model: str = ""
    timeout: int = 600
    root: Path | None = None  # judge-gate criterion resolution root (falls back to cwd)


def run_command_gate(gate: GateSpec, ctx: GateContext) -> GateVerdict:
    # start_new_session=True (F6) so a timeout can kill the whole process
    # group — a shell that spawned children cannot leave them behind.
    # encoding+errors='replace' avoids a UnicodeDecodeError on garbled output.
    proc = subprocess.Popen(
        gate.run,
        shell=True,
        cwd=ctx.cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=ctx.timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=5)
        return GateVerdict(
            "fail", [Finding(summary=f"`{gate.run}` timed out after {ctx.timeout}s")]
        )
    if proc.returncode == 0:
        return GateVerdict("pass")
    output = tail(stdout + "\n" + stderr, 4000)
    return GateVerdict(
        "fail",
        [Finding(summary=f"`{gate.run}` exited {proc.returncode}", detail=output)],
    )


def _criterion_for(gate: GateSpec, ctx: GateContext) -> str:
    if gate.rubric:
        return gate.rubric
    if gate.criterion:
        body = resolve_prompt(ctx.root or ctx.cwd, "prompts", gate.criterion)
        if body is None:
            raise DriverError(
                f"judge gate {gate.name!r}: criterion {gate.criterion!r} not found in prompts/"
            )
        return body
    raise DriverError(f"judge gate {gate.name!r}: neither 'rubric' nor 'criterion' is set")


def run_judged_gate(gate: GateSpec, ctx: GateContext) -> GateVerdict:
    criterion = _criterion_for(gate, ctx)
    prompt = (
        JUDGE_PREAMBLE.format(schema=json.dumps(VERDICT_SCHEMA, indent=2))
        + f"\n## Criterion — gate {gate.name!r}\n\n{criterion}\n"
        + f"\n## Material under judgment\n\n{ctx.material}\n"
    )
    last_error = ""
    for attempt in range(2):  # one retry, then fail closed
        ask = prompt if attempt == 0 else (
            prompt + f"\n\nYour previous output was invalid: {last_error}. "
            "Emit ONLY the JSON object."
        )
        try:
            data = ctx.judge.run(ask, VERDICT_SCHEMA, cwd=ctx.cwd, model=ctx.judge_model)
            return parse_verdict(data)
        except (DriverError, EnvelopeError) as exc:
            last_error = str(exc)
    return GateVerdict(
        "fail",
        [Finding(summary=f"judge for gate {gate.name!r} failed closed", detail=last_error)],
    )


@dataclass
class GateRun:
    """One pass over a gate set: what each gate said, and everything wrong."""

    verdicts: dict[str, str]  # gate name -> "pass" | "fail"
    findings: list[Finding]

    @property
    def passed(self) -> bool:
        return all(v == "pass" for v in self.verdicts.values())


def run_gate_set(gates: list[GateSpec], ctx: GateContext) -> GateRun:
    """Run the full set; collect findings from failing gates only.

    Green is decided by the verdict, not by whether findings exist: a gate that
    passes contributes nothing, so a judge answering ``pass`` with advisory
    notes never blocks the loop or feeds noise to the next worker. Every gate
    runs — nothing short-circuits — so a failed attempt sees the whole picture.

    The per-gate verdicts are kept as well as the findings: the loops narrate
    them into the Ledger, where a failed attempt's story is "which gate said
    no", not just "something did".
    """
    findings: list[Finding] = []
    verdicts: dict[str, str] = {}
    for gate in gates:
        if gate.type == "command":
            verdict = run_command_gate(gate, ctx)
        elif gate.type == "judge":
            verdict = run_judged_gate(gate, ctx)
        else:
            raise DriverError(f"gate {gate.name!r}: unknown type {gate.type!r}")
        verdicts[gate.name] = verdict.verdict
        if verdict.passed:
            continue
        for finding in verdict.findings:
            finding.gate = gate.name
        findings.extend(verdict.findings)
    return GateRun(verdicts=verdicts, findings=findings)


def run_gates(gates: list[GateSpec], ctx: GateContext) -> tuple[bool, list[Finding]]:
    """The gate set's verdict and findings, for callers that need nothing else."""
    run = run_gate_set(gates, ctx)
    return (run.passed, run.findings)


def build_context(cfg: Config, cwd: Path, material: str, judge: Driver) -> GateContext:
    entry = cfg.roster_for("judge")
    return GateContext(
        cwd=cwd,
        material=material,
        judge=judge,
        judge_model=entry.model,
        timeout=cfg.gate_timeout,
        root=cfg.root,
    )
