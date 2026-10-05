from pathlib import Path

from giro.config import GateSpec
from giro.drivers import DriverError, FakeDriver
from giro.gates import GateContext, run_gates


def ctx(tmp_path: Path, judge=None) -> GateContext:
    return GateContext(
        cwd=tmp_path,
        material="diff …",
        judge=judge or FakeDriver([]),
        timeout=30,
    )


def test_command_gate_pass_and_fail(tmp_path):
    gates = [
        GateSpec(name="yes", type="command", run="true"),
        GateSpec(name="no", type="command", run="echo boom >&2; exit 3"),
    ]
    green, findings = run_gates(gates, ctx(tmp_path))
    assert not green
    assert len(findings) == 1
    assert findings[0].gate == "no"
    assert "exited 3" in findings[0].summary
    assert "boom" in findings[0].detail


def test_all_gates_run_findings_collected(tmp_path):
    gates = [
        GateSpec(name="a", type="command", run="false"),
        GateSpec(name="b", type="command", run="false"),
    ]
    _, findings = run_gates(gates, ctx(tmp_path))
    assert {f.gate for f in findings} == {"a", "b"}


def test_judge_gate_pass(tmp_path):
    judge = FakeDriver([{"verdict": "pass"}])
    gates = [GateSpec(name="review", type="judge", rubric="Looks right.")]
    green, findings = run_gates(gates, ctx(tmp_path, judge))
    assert green and findings == []
    assert "Looks right." in judge.calls[0]["prompt"]
    assert "diff …" in judge.calls[0]["prompt"]


def test_pass_verdict_with_advisory_findings_stays_green(tmp_path):
    # a small judge that answers "pass" but tacks on notes must not block the loop
    judge = FakeDriver([{"verdict": "pass", "findings": [{"summary": "advisory: looks fine"}]}])
    gates = [GateSpec(name="review", type="judge", rubric="r")]
    green, findings = run_gates(gates, ctx(tmp_path, judge))
    assert green and findings == []  # verdict decides green; a pass feeds no findings


def test_judge_gate_fail_carries_findings(tmp_path):
    judge = FakeDriver([{"verdict": "fail", "findings": [{"summary": "missing test"}]}])
    gates = [GateSpec(name="review", type="judge", rubric="r")]
    green, findings = run_gates(gates, ctx(tmp_path, judge))
    assert not green and findings[0].summary == "missing test"
    assert findings[0].gate == "review"


def test_judge_malformed_retries_once_then_fails_closed(tmp_path):
    judge = FakeDriver([{"nonsense": True}, {"still": "wrong"}])
    gates = [GateSpec(name="review", type="judge", rubric="r")]
    green, findings = run_gates(gates, ctx(tmp_path, judge))
    assert not green
    assert "failed closed" in findings[0].summary
    assert len(judge.calls) == 2
    assert "previous output was invalid" in judge.calls[1]["prompt"]


def test_judge_driver_error_fails_closed(tmp_path):
    judge = FakeDriver([DriverError("boom"), DriverError("boom again")])
    gates = [GateSpec(name="review", type="judge", rubric="r")]
    green, findings = run_gates(gates, ctx(tmp_path, judge))
    assert not green and "failed closed" in findings[0].summary


def test_judge_gate_reads_criterion_from_prompts_dir(tmp_path):
    (tmp_path / "prompts").mkdir(parents=True)
    (tmp_path / "prompts" / "deep-review.md").write_text("# Deep review\nCheck invariants.")
    judge = FakeDriver([{"verdict": "pass"}])
    gates = [GateSpec(name="deep", type="judge", criterion="deep-review")]
    green, _ = run_gates(gates, ctx(tmp_path, judge))
    assert green
    assert "Check invariants." in judge.calls[0]["prompt"]
