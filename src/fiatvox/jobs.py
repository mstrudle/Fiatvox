"""One book from source file to audiobook: prepare, (review stop), narrate, report, import.

Shared by `fiatvox make` (which prints the events) and the app's background worker (which sends them to the
screen as JSON lines). Failures go through the AI troubleshooter; every failure leaves an error report.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

Emit = Callable[[dict], None]

# exit codes for the worker process
OK, FAILED, REVIEW, CANCELLED = 0, 1, 10, 130


@dataclass
class Job:
    source: str
    voice: str = ""
    out_dir: str = ""
    review_stop: bool = False       # pause after the first chapter for the listener to check it
    no_ai: bool = False
    provider: str | None = None
    model: str | None = None
    review_rounds: int | None = None
    note: str = ""
    force_ocr: bool = False
    ocr: str | None = None
    redecide: bool = False
    fresh: bool = False
    prepare_only: bool = False
    sections: list[int] | None = None   # zero-based; None means every chapter
    import_to_books: bool | None = None
    qc_report: bool | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Job":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def book_id(src: Path) -> str:
    import hashlib
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", src.stem.lower()).strip("-")[:40] or "book"
    return f"{slug}-{hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]}"


def review_section(book) -> int:
    """The chapter the listener checks: the first numbered chapter, else the first with 300 or more words."""
    import re
    for i, s in enumerate(book.sections):
        if re.match(r"(chapter|lecture)\b", s.title, re.I) and s.words >= 100:
            return i
    for i, s in enumerate(book.sections):
        if s.words >= 300:
            return i
    return 0


@dataclass
class Result:
    code: int
    file: str = ""
    report: str = ""
    error: str = ""
    preview: str = ""
    extra: dict = field(default_factory=dict)


def run(job: Job, emit: Emit, cancelled: Callable[[], bool] = lambda: False) -> Result:
    from . import troubleshoot as ts
    from .ai import get_backend
    from .finish import books, report
    from .ingest.document import SUFFIXES as DOC_SUFFIXES
    from .narrate.assemble import safe_filename
    from .narrate.kokoro_engine import resolve_voice
    from .narrate.narrator import Cancelled, narrate
    from .prepare.pipeline import PrepOptions, prepare, reset
    from .settings import Settings

    st = Settings.load()
    src = Path(job.source).expanduser().resolve()
    if not src.exists() or src.suffix.lower() not in (".epub", ".pdf") + DOC_SUFFIXES:
        msg = f"Not a book file Fiat Vox can read (EPUB, PDF, RTF, DOCX, ODT or text): {src}"
        emit({"type": "error", "msg": msg})
        return Result(FAILED, error=msg)
    voice = resolve_voice(job.voice or st.default_voice)
    out_dir = Path(job.out_dir or st.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = paths.book_dir(book_id(src))
    if job.fresh:
        reset(work)
    provider = "none" if job.no_ai else (job.provider or st.ai_provider)
    backend = get_backend(provider, job.model if job.model is not None else st.ai_model)
    backend.usage.on_change = lambda snap: emit({"type": "usage", **snap})

    def log(msg: str) -> None:
        emit({"type": "log", "msg": msg})

    def progress(stage, frac, msg):
        if cancelled():
            raise Cancelled()
        emit({"type": "stage", "stage": stage, "frac": frac, "msg": msg})

    t0 = time.monotonic()
    opts = PrepOptions(review_rounds=st.review_rounds if job.review_rounds is None else job.review_rounds,
                       redecide=job.redecide, force_ocr=job.force_ocr, ocr_engine=job.ocr or st.ocr_engine,
                       note=job.note or "")
    kind = "pdf" if src.suffix.lower() == ".pdf" else "text"
    incident = ts.Incident(book=work.name, source=str(src))
    state: dict = {"stage": "preparing", "reprepare": False, "book": None, "qc": None, "out": None}

    def context():
        dec_path = work / "decisions.json"
        dec = json.loads(dec_path.read_text(encoding="utf-8")) if dec_path.exists() else {}
        return {"stage": state["stage"], "file": src.name, "kind": src.suffix.lower()[1:],
                "size_mb": round(src.stat().st_size / 1e6, 1), "voice": voice,
                "ai": backend.label, "options": vars(opts), "work_files": sorted(f.name for f in work.iterdir()),
                "decisions": {k: (v if k in ("pages", "drop", "flags") else len(v) if isinstance(v, (list, dict))
                                  else v) for k, v in dec.items() if not k.startswith("_")},
                "warnings": (state["qc"] or {}).get("warnings", [])[:10]}

    def apply(remedy: str, params: dict) -> str:
        return apply_remedy(remedy, params, kind, work, opts, state)

    def do_prepare():
        state["stage"] = "preparing"
        state["book"], state["qc"] = prepare(src, work, backend, opts, progress)
        state["reprepare"] = False
        return state["book"]

    never = (KeyboardInterrupt, Cancelled)
    try:
        log(f"Preparing {src.name} (AI: {backend.label})")
        ts.run_with_recovery("preparing the book", do_prepare, apply, backend, incident, context, log, never)
    except never:
        emit({"type": "cancelled"})
        return Result(CANCELLED)
    except ts.GaveUp as e:
        emit({"type": "error", "msg": f"Could not prepare the book: {e}", "report": e.report})
        return Result(FAILED, error=str(e), report=e.report)
    book, qc = state["book"], state["qc"]
    for w in qc["warnings"]:
        log(f"Warning: {w}")
    emit({"type": "prepared", "title": book.title, "author": book.author, "chapters": len(book.sections),
          "voice": voice, "out_dir": str(out_dir), "ai": backend.label,
          "words": book.words, "seconds": round(time.monotonic() - t0), "work": str(work),
          "titles": [s.title for s in book.sections]})
    if job.prepare_only:
        _close(incident, context, log)
        return Result(OK)

    def nprog(ev):
        if cancelled():
            raise Cancelled()
        if ev.get("stage") == "assembling":
            emit({"type": "assembling", "done": ev.get("done"), "total": ev.get("total")})
            return
        if ev.get("event") == "chapter_done":
            emit({"type": "chapter_done", **{k: v for k, v in ev.items() if k != "event"}})
            return
        emit({"type": "narrating", **{k: v for k, v in ev.items() if k != "event"}})

    # the review stop: narrate one chapter on its own and pause
    if job.review_stop:
        k = review_section(book)
        preview = work / "review-preview.m4a"
        state["stage"] = "narrating the review chapter"
        try:
            ts.run_with_recovery("narrating the review chapter",
                                 lambda: narrate(state["book"], voice, preview, st.silences(), st.bitrate_kbps,
                                                 progress=nprog, sections=[k]),
                                 apply, backend, incident, context, log, never)
        except never:
            emit({"type": "cancelled"})
            return Result(CANCELLED)
        except ts.GaveUp as e:
            emit({"type": "error", "msg": f"Could not narrate the review chapter: {e}", "report": e.report})
            return Result(FAILED, error=str(e), report=e.report)
        emit({"type": "review", "preview": str(preview), "chapter": book.sections[k].title, "section": k,
              "voice": voice})
        return Result(REVIEW, preview=str(preview))

    def do_narrate():
        if state["reprepare"]:
            do_prepare()
        state["stage"] = "narrating"
        b = state["book"]
        name = safe_filename(f"{b.author} - {b.title}" if b.author else b.title or src.stem)
        state["out"] = out_dir / f"{name}.m4b"
        log(f"Narrating with {voice} to {state['out']}")
        return narrate(b, voice, state["out"], st.silences(), st.bitrate_kbps, progress=nprog,
                       sections=job.sections, cancelled=cancelled)

    try:
        nar = ts.run_with_recovery("narrating the book", do_narrate, apply, backend, incident, context, log, never)
    except never:
        emit({"type": "cancelled"})
        return Result(CANCELLED)
    except ts.GaveUp as e:
        emit({"type": "error", "msg": f"Could not narrate the book: {e}", "report": e.report})
        return Result(FAILED, error=str(e), report=e.report)
    _close(incident, context, log)
    book, qc, out = state["book"], state["qc"], state["out"]
    rp = ""
    if (st.qc_report if job.qc_report is None else job.qc_report):
        rp = str(report.write(out.with_name(out.stem + " - QC report.md"), qc, nar))
    imported = False
    if st.import_to_books if job.import_to_books is None else job.import_to_books:
        imported, err = books.import_to_books(out)
        if not imported:
            log(f"Could not import into Books: {err}")
    if st.notify:
        books.notify("Fiat Vox", f"{book.title or src.stem} is ready.")
    emit({"type": "done", "file": str(out), "report": rp, "chapters": len(nar["chapters"]),
          "duration": nar["duration"], "imported": imported, "usage": backend.usage.snapshot()})
    return Result(OK, file=str(out), report=rp)


def _close(incident, context, log) -> None:
    if incident.attempts:
        incident.outcome = "recovered"
        log(f"Recovered after {len(incident.attempts)} failure(s). Report: {incident.write(context())}")


def apply_remedy(remedy: str, params: dict, kind: str, work: Path, opts, state: dict) -> str:
    """Carry out one remedy from the troubleshooter's menu, for this run of this book only."""
    from .ingest import ocr as ocr_mod
    dec_path = work / "decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8")) if dec_path.exists() else None
    state["reprepare"] = True
    if remedy == "retry":
        return "the step runs again unchanged"
    if remedy == "force_ocr":
        if kind != "pdf":
            raise ValueError("only a PDF can be recognised again")
        opts.force_ocr, opts.redecide = True, True
        return "every page will be recognised again, then the chapters decided again"
    if remedy == "ocr_engine":
        eng = params.get("engine")
        if kind != "pdf" or eng not in ("vision", "tesseract") or not ocr_mod.ocr_available(eng):
            raise ValueError(f"OCR engine {eng!r} is not available for this book")
        opts.ocr_engine, opts.force_ocr, opts.redecide = eng, True, True
        return f"OCR with {eng}"
    if remedy == "redecide":
        opts.redecide = True
        return "the AI decides chapters and cuts again"
    if remedy == "heuristics":
        opts.heuristics, opts.redecide = True, True
        return "chapters and cuts decided with local rules"
    if remedy == "no_review":
        opts.review_rounds = 0
        return "review rounds skipped"
    if remedy == "no_ai_correction":
        opts.ai_correction = False
        return "AI correction of scanning errors skipped"
    if dec is None:
        raise ValueError("there are no saved decisions to change yet")
    if remedy == "drop_sections":
        if kind == "pdf":
            raise ValueError("a PDF has pages, not sections")
        extra = [int(x) for x in params.get("sections") or [] if str(x).isdigit()]
        if not extra:
            raise ValueError("no section numbers given")
        dec["drop"] = sorted(set(dec.get("drop") or []) | set(extra))
        result = f"sections {extra} cut"
    elif remedy == "keep_pages":
        from .ingest.pdf import parse_pages
        if kind != "pdf":
            raise ValueError("only a PDF has pages")
        pages = str(params.get("pages") or "")
        if not parse_pages(pages, 100000):
            raise ValueError("the page list is empty or unreadable")
        dec["pages"] = pages
        result = f"keeping pages {pages}"
    elif remedy == "replace_text":
        find, rep = str(params.get("find") or ""), str(params.get("replace") or "")
        if not find.strip() or len(find) > 120:
            raise ValueError("the text to replace is empty or too long")
        dec.setdefault("replacements", []).append([find, rep])
        result = f'replacing "{find[:50]}" with "{rep[:50]}"'
    else:
        raise ValueError(f"unknown remedy {remedy}")
    dec_path.write_text(json.dumps(dec, ensure_ascii=False, indent=1), encoding="utf-8")
    opts.redecide = False
    return result
