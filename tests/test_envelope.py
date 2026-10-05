import pytest

from giro.envelope import (
    EnvelopeError,
    parse_plan,
    parse_verdict,
    parse_worker,
)


def test_verdict_pass():
    verdict = parse_verdict({"verdict": "pass"})
    assert verdict.passed and verdict.findings == []


def test_verdict_fail_collects_findings():
    verdict = parse_verdict(
        {"verdict": "fail", "findings": [{"summary": "broken", "location": "a.py:3"}]}
    )
    assert not verdict.passed
    assert verdict.findings[0].summary == "broken"
    assert verdict.findings[0].location == "a.py:3"


def test_verdict_fail_without_findings_gets_placeholder():
    verdict = parse_verdict({"verdict": "fail"})
    assert verdict.findings and "without findings" in verdict.findings[0].summary


@pytest.mark.parametrize(
    "data",
    [
        "not a dict",
        {},
        {"verdict": "maybe"},
        {"verdict": "fail", "findings": [{"detail": "no summary"}]},
        {"verdict": "fail", "findings": "nope"},
    ],
)
def test_verdict_fail_closed_on_malformed(data):
    with pytest.raises(EnvelopeError):
        parse_verdict(data)


def test_worker_result():
    result = parse_worker({"outcome": "completed", "summary": "did it", "notes": "n"})
    assert result.outcome == "completed" and result.notes == "n"


@pytest.mark.parametrize(
    "data", [{}, {"outcome": "done", "summary": "x"}, {"outcome": "completed"}]
)
def test_worker_malformed(data):
    with pytest.raises(EnvelopeError):
        parse_worker(data)


def test_plan_dependencies_validated():
    plan = parse_plan(
        {
            "issues": [
                {"title": "A", "body": "a"},
                {"title": "B", "body": "b", "blocked_by": [1]},
            ]
        }
    )
    assert plan[1].blocked_by == [1]
    with pytest.raises(EnvelopeError):
        parse_plan({"issues": [{"title": "A", "body": "a", "blocked_by": [1]}]})
    with pytest.raises(EnvelopeError):
        parse_plan({"issues": []})


def test_plan_rejects_dependency_cycle():
    with pytest.raises(EnvelopeError, match="cycle"):
        parse_plan(
            {
                "issues": [
                    {"title": "A", "body": "a", "blocked_by": [2]},
                    {"title": "B", "body": "b", "blocked_by": [1]},
                ]
            }
        )
