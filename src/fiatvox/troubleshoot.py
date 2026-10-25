"""When a step fails, ask the AI what went wrong and try one of a fixed set of remedies, at most three times.

The AI never edits code, files or settings and never runs commands. It returns JSON naming one remedy from the
menu below; Python checks it and applies it to this book's run only. Every failure, with each diagnosis and what
happened, is written to an error report in the data folder's `errors/` folder, so the causes can be fixed in the
code later (`fiatvox errors` lists them).
"""
from __future__ import annotations

import datetime
import json
import platform
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, paths
from .ai import NoAI, ask_json

MAX_QUERIES = 3

REMEDIES = {
    "retry": "run the failed step again unchanged (for a passing problem: a network error, a busy disk)",
    "force_ocr": "PDF only: ignore the PDF's text layer and recognise every page again",
    "ocr_engine": 'PDF only: switch OCR engine; params {"engine": "vision" or "tesseract"}',
    "redecide": "ask the AI again where chapters start and what to cut (the saved decisions may be the problem)",
    "heuristics": "decide chapters and cuts with the local rules instead of the AI",
    "no_review": "skip the AI review rounds",
    "no_ai_correction": "skip the AI correction of scanning errors",
    "drop_sections": 'EPUB or document: also cut these section numbers; params {"sections": [3, 4]}',
    "keep_pages": 'PDF: the complete list of pages to keep; params {"pages": "11-16,18-299"}',
    "replace_text": 'replace text the narrator cannot read; params {"find": "exact text", "replace": "new text"}',
    "give_up": "nothing on this list will help; explain why in the diagnosis",
}

PROMPT = """Fiat Vox, a program that turns books into audiobooks, failed while processing a book. Diagnose the failure
from the evidence below and choose ONE remedy from this list. You cannot run commands or change any code, files or
settings yourself: the program applies the remedy you choose to this book's run and tries again.

REMEDIES:
{remedies}

Choose "give_up" if the failure is a bug in the program that no remedy can work around; say in the diagnosis what
the code should do differently, since your diagnosis goes into an error report for the developers. Do not choose
a remedy that an earlier attempt already tried unless the evidence has changed.

Return ONLY JSON: {{"diagnosis": "what went wrong and why", "remedy": "one name from the list", "params": {{}},
"confidence": "high" | "medium" | "low"}}

EVIDENCE
{evidence}
"""


@dataclass
class Attempt:
    stage: str
    error: str
    trace: str
    diagnosis: str = ""
    remedy: str = ""
    params: dict = field(default_factory=dict)
    applied: str = ""


@dataclass
class Incident:
    book: str
    source: str
    started: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))
    attempts: list[Attempt] = field(default_factory=list)
    outcome: str = ""
    report: str = ""

    def write(self, context: dict) -> Path:
        """The error report: markdown for people, JSON beside it for tools."""
        d = paths.ensure(paths.data_dir() / "errors")
        stamp = self.started.replace(":", "").replace("-", "")
        base = d / f"{stamp}-{self.book}"
        L = [f"# Fiat Vox error report: {Path(self.source).name}", "",
             f"- **When:** {self.started}", f"- **Outcome:** {self.outcome or 'in progress'}",
             f"- **Fiat Vox:** {__version__}; macOS {platform.mac_ver()[0]}; Python {platform.python_version()}",
             f"- **Source:** `{self.source}`", ""]
        L += ["## Context", "", "```json", json.dumps(context, indent=1, ensure_ascii=False, default=str), "```", ""]
        for i, a in enumerate(self.attempts, 1):
            L += [f"## Failure {i}: {a.stage}", "", f"**Error:** `{a.error}`", ""]
            if a.diagnosis:
                L += [f"**AI diagnosis:** {a.diagnosis}", "", f"**Remedy chosen:** `{a.remedy}` {json.dumps(a.params)}",
                      "", f"**Result:** {a.applied}", ""]
            L += ["```", a.trace.strip()[-6000:], "```", ""]
        base.with_suffix(".md").write_text("\n".join(L) + "\n", encoding="utf-8")
        base.with_suffix(".json").write_text(json.dumps({
            "book": self.book, "source": self.source, "started": self.started, "outcome": self.outcome,
            "context": context, "attempts": [a.__dict__ for a in self.attempts]}, indent=1, default=str),
            encoding="utf-8")
        self.report = str(base.with_suffix(".md"))
        return base.with_suffix(".md")


def evidence(stage: str, exc: BaseException, context: dict, history: list[Attempt]) -> str:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    L = [f"FAILED STEP: {stage}", f"ERROR: {type(exc).__name__}: {exc}", "TRACEBACK (last part):", tb[-4000:],
         "CONTEXT: " + json.dumps(context, ensure_ascii=False, default=str)[:4000]]
    if history:
        L.append("EARLIER ATTEMPTS IN THIS RUN:")
        for a in history:
            L.append(f"- {a.stage}: {a.error[:200]} -> remedy {a.remedy} {a.params}: {a.applied}")
    return "\n".join(L)


def diagnose(backend, stage: str, exc: BaseException, context: dict, history: list[Attempt]) -> dict:
    remedies = "\n".join(f"- {k}: {v}" for k, v in REMEDIES.items())
    out = ask_json(backend, PROMPT.format(remedies=remedies, evidence=evidence(stage, exc, context, history)),
                   purpose="troubleshoot", timeout=300)
    remedy = str(out.get("remedy") or "give_up")
    if remedy not in REMEDIES:
        remedy = "give_up"
    params = out.get("params") if isinstance(out.get("params"), dict) else {}
    return {"diagnosis": str(out.get("diagnosis") or "")[:2000], "remedy": remedy, "params": params}


class GaveUp(RuntimeError):
    def __init__(self, msg: str, report: str):
        super().__init__(msg)
        self.report = report


def run_with_recovery(stage: str, step: Callable[[], object], apply: Callable[[str, dict], str], backend,
                      incident: Incident, context: Callable[[], dict],
                      say: Callable[[str], None] = print, never: tuple = ()) -> object:
    """Run `step`; on failure ask the AI (at most MAX_QUERIES times in the whole incident) for a remedy, let
    `apply` carry it out (it returns a description, or raises ValueError if the remedy does not fit), and retry.
    Exceptions listed in `never` (cancellation) pass straight through."""
    while True:
        try:
            return step()
        except never:
            raise
        except Exception as exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            att = Attempt(stage, f"{type(exc).__name__}: {exc}"[:1000], tb)
            history = list(incident.attempts)
            incident.attempts.append(att)
            asked = sum(1 for a in incident.attempts if a.diagnosis)
            if isinstance(backend, NoAI) or not backend.available()[0]:
                att.applied = "no AI available to troubleshoot"
                incident.outcome = "failed"
                raise GaveUp(f"{stage} failed: {exc}", str(incident.write(context()))) from exc
            if asked >= MAX_QUERIES:
                att.applied = f"not attempted: the limit of {MAX_QUERIES} AI diagnoses was reached"
                incident.outcome = "failed after the AI's remedies"
                raise GaveUp(f"{stage} failed: {exc}", str(incident.write(context()))) from exc
            say(f"{stage} failed ({type(exc).__name__}: {str(exc)[:160]}). Asking the AI to troubleshoot "
                f"({asked + 1} of {MAX_QUERIES})...")
            try:
                d = diagnose(backend, stage, exc, context(), history)
            except Exception as e2:
                att.diagnosis = f"(the AI could not be asked: {e2})"
                att.applied = "no remedy"
                incident.outcome = "failed"
                raise GaveUp(f"{stage} failed: {exc}", str(incident.write(context()))) from exc
            att.diagnosis, att.remedy, att.params = d["diagnosis"], d["remedy"], d["params"]
            say(f"AI: {att.diagnosis[:300]}")
            if att.remedy == "give_up":
                att.applied = "the AI found no remedy"
                incident.outcome = "failed: the AI found no remedy"
                raise GaveUp(f"{stage} failed: {exc}", str(incident.write(context()))) from exc
            try:
                att.applied = apply(att.remedy, att.params)
            except ValueError as e3:
                att.applied = f"remedy refused: {e3}"
                say(f"The remedy '{att.remedy}' does not fit this book ({e3}).")
                incident.write(context())
                continue  # counts as one of the three queries; the step is retried as it was
            say(f"Trying again with remedy '{att.remedy}': {att.applied}")
            incident.write(context())


def list_reports(limit: int = 20) -> list[dict]:
    d = paths.data_dir() / "errors"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json"), reverse=True)[:limit]:
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"file": str(f.with_suffix(".md")), "book": Path(j["source"]).name, "when": j["started"],
                    "outcome": j["outcome"], "steps": [a["stage"] for a in j["attempts"]],
                    "first_error": j["attempts"][0]["error"] if j["attempts"] else ""})
    return out
