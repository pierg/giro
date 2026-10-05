"""Envelopes — the only things that cross back from an LLM context.

Every spawned context ends by emitting one JSON envelope. The engine
schema-checks it here; a malformed envelope is an error the caller turns
into a failed attempt or a failed gate (fail closed), never a shrug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WORKER_OUTCOMES = frozenset({"completed", "needs-human", "failed"})
VERDICTS = frozenset({"pass", "fail"})


class EnvelopeError(Exception):
    """The context's final output did not match the required schema."""


@dataclass
class Finding:
    summary: str
    detail: str = ""
    location: str = ""
    gate: str = ""

    def as_dict(self) -> dict[str, str]:
        out = {"summary": self.summary}
        if self.detail:
            out["detail"] = self.detail
        if self.location:
            out["location"] = self.location
        if self.gate:
            out["gate"] = self.gate
        return out


@dataclass
class GateVerdict:
    verdict: str  # "pass" | "fail"
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


@dataclass
class WorkerResult:
    outcome: str  # "completed" | "needs-human" | "failed"
    summary: str
    notes: str = ""


def _require(data: Any, key: str, kind: type) -> Any:
    if not isinstance(data, dict):
        raise EnvelopeError(f"envelope must be a JSON object, got {type(data).__name__}")
    if key not in data:
        raise EnvelopeError(f"envelope missing required key {key!r}")
    value = data[key]
    if not isinstance(value, kind):
        raise EnvelopeError(f"envelope key {key!r} must be {kind.__name__}")
    return value


def parse_findings(raw: Any) -> list[Finding]:
    if not isinstance(raw, list):
        raise EnvelopeError("'findings' must be a list")
    findings: list[Finding] = []
    for item in raw:
        summary = _require(item, "summary", str)
        findings.append(
            Finding(
                summary=summary,
                detail=str(item.get("detail", "")),
                location=str(item.get("location", "")),
            )
        )
    return findings


def parse_verdict(data: Any) -> GateVerdict:
    verdict = _require(data, "verdict", str)
    if verdict not in VERDICTS:
        raise EnvelopeError(f"'verdict' must be one of {sorted(VERDICTS)}, got {verdict!r}")
    findings = parse_findings(data.get("findings", []))
    if verdict == "fail" and not findings:
        findings = [Finding(summary="gate failed without findings")]
    return GateVerdict(verdict=verdict, findings=findings)


def parse_worker(data: Any) -> WorkerResult:
    outcome = _require(data, "outcome", str)
    if outcome not in WORKER_OUTCOMES:
        raise EnvelopeError(f"'outcome' must be one of {sorted(WORKER_OUTCOMES)}, got {outcome!r}")
    summary = _require(data, "summary", str)
    return WorkerResult(outcome=outcome, summary=summary, notes=str(data.get("notes", "")))


@dataclass
class PlannedIssue:
    title: str
    body: str
    blocked_by: list[int] = field(default_factory=list)  # 1-based indices into the plan


def parse_plan(data: Any) -> list[PlannedIssue]:
    raw = _require(data, "issues", list)
    if not raw:
        raise EnvelopeError("'issues' must contain at least one issue")
    planned: list[PlannedIssue] = []
    for item in raw:
        title = _require(item, "title", str)
        body = _require(item, "body", str)
        blocked = item.get("blocked_by", [])
        if not isinstance(blocked, list) or not all(isinstance(b, int) for b in blocked):
            raise EnvelopeError("'blocked_by' must be a list of integers (1-based plan indices)")
        planned.append(PlannedIssue(title=title, body=body, blocked_by=list(blocked)))
    for i, issue in enumerate(planned, start=1):
        for dep in issue.blocked_by:
            if dep < 1 or dep > len(planned) or dep == i:
                raise EnvelopeError(f"plan issue {i} has invalid dependency index {dep}")
    _reject_plan_cycles(planned)
    return planned


def _reject_plan_cycles(planned: list[PlannedIssue]) -> None:
    """A blocked_by cycle can never reach a schedulable frontier — fail closed."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in range(1, len(planned) + 1)}

    def visit(node: int) -> None:
        color[node] = GREY
        for dep in planned[node - 1].blocked_by:
            if color[dep] == GREY:
                raise EnvelopeError(f"plan has a blocked_by cycle involving issue {dep}")
            if color[dep] == WHITE:
                visit(dep)
        color[node] = BLACK

    for n in range(1, len(planned) + 1):
        if color[n] == WHITE:
            visit(n)


# JSON Schemas handed to drivers that support structured output, and embedded
# in prompts for those that do not.

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdict"],
    "properties": {
        "verdict": {"enum": ["pass", "fail"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["summary"],
                "properties": {
                    "summary": {"type": "string"},
                    "detail": {"type": "string"},
                    "location": {"type": "string"},
                },
            },
        },
    },
}

WORKER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["outcome", "summary"],
    "properties": {
        "outcome": {"enum": ["completed", "needs-human", "failed"]},
        "summary": {"type": "string"},
        "notes": {"type": "string"},
    },
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["issues"],
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "body"],
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "blocked_by": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}
