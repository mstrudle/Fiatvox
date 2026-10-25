"""When a step fails: at most three AI diagnoses, remedies only from the fixed menu, always an error report."""
import json

import pytest

from fiatvox import troubleshoot as ts
from fiatvox.ai import NoAI
from fiatvox.ai.base import Usage


class Mock:
    name, label = "mock", "mock"

    def __init__(self, remedies):
        self.remedies = list(remedies)
        self.usage = Usage()
        self.prompts = []

    def available(self):
        return True, ""

    def complete(self, prompt, *, purpose, timeout=900):
        self.prompts.append(prompt)
        r = self.remedies.pop(0)
        return json.dumps({"diagnosis": f"diagnosis for {r}", "remedy": r, "params": {}})


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("FIATVOX_DATA", str(tmp_path))


def flaky(fail_times):
    calls = {"n": 0}

    def step():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise RuntimeError(f"boom {calls['n']}")
        return "ok"
    return step, calls


def test_recovers_after_a_remedy():
    step, calls = flaky(1)
    applied = []
    inc = ts.Incident("b", "/x/book.pdf")
    out = ts.run_with_recovery("preparing", step, lambda r, p: applied.append(r) or "done", Mock(["force_ocr"]),
                               inc, lambda: {"k": 1}, say=lambda m: None)
    assert out == "ok" and applied == ["force_ocr"] and calls["n"] == 2
    assert inc.attempts[0].diagnosis == "diagnosis for force_ocr"
    assert "RuntimeError: boom 1" in inc.attempts[0].error
    assert inc.report and open(inc.report).read().startswith("# Fiat Vox error report: book.pdf")


def test_at_most_three_queries():
    step, calls = flaky(10)
    m = Mock(["retry", "redecide", "no_review", "retry"])
    inc = ts.Incident("b", "/x/book.epub")
    with pytest.raises(ts.GaveUp) as e:
        ts.run_with_recovery("preparing", step, lambda r, p: "done", m, inc, dict, say=lambda m: None)
    assert len(m.prompts) == 3 and calls["n"] == 4
    assert "limit of 3" in inc.attempts[-1].applied
    rep = json.loads(open(e.value.report.replace(".md", ".json")).read())
    assert rep["outcome"] == "failed after the AI's remedies" and len(rep["attempts"]) == 4


def test_unknown_remedy_becomes_give_up():
    step, _ = flaky(5)
    m = Mock(["rm -rf /"])
    with pytest.raises(ts.GaveUp):
        ts.run_with_recovery("narrating", step, lambda r, p: "done", m, ts.Incident("b", "/x/b.pdf"), dict,
                             say=lambda m: None)
    assert len(m.prompts) == 1


def test_refused_remedy_counts_and_retries():
    step, calls = flaky(1)

    def apply(remedy, params):
        if remedy == "keep_pages":
            raise ValueError("only a PDF has pages")
        return "done"
    inc = ts.Incident("b", "/x/b.epub")
    ts.run_with_recovery("preparing", step, apply, Mock(["keep_pages", "retry"]), inc, dict, say=lambda m: None)
    assert inc.attempts[0].applied.startswith("remedy refused")


def test_no_ai_writes_a_report_and_stops():
    step, calls = flaky(1)
    with pytest.raises(ts.GaveUp) as e:
        ts.run_with_recovery("preparing", step, lambda r, p: "x", NoAI(), ts.Incident("b", "/x/b.pdf"), dict,
                             say=lambda m: None)
    assert calls["n"] == 1 and e.value.report.endswith(".md")


def test_cancel_passes_straight_through():
    def step():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        ts.run_with_recovery("narrating", step, lambda r, p: "x", Mock([]), ts.Incident("b", "/x/b.pdf"), dict,
                             say=lambda m: None, never=(KeyboardInterrupt,))


def test_reports_are_listed():
    step, _ = flaky(1)
    with pytest.raises(ts.GaveUp):
        ts.run_with_recovery("preparing", step, lambda r, p: "x", NoAI(), ts.Incident("b", "/x/b.pdf"), dict,
                             say=lambda m: None)
    reps = ts.list_reports()
    assert len(reps) == 1 and reps[0]["book"] == "b.pdf" and "boom 1" in reps[0]["first_error"]
