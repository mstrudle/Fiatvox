"""Prepare one book for narration: ingest, decisions, build, OCR correction, review rounds, chapter names.

Ported from audiobook.make and audiobook.build. Everything intermediate goes in the book's work folder:
  ocr.json, dossier.json, decisions.json, ai-cache.json, ai-log.md, raw.json (the whole book as read),
  book.json (the finished book), qc.json (measurements for the report).
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..ai import AIError, NoAI, ask_json
from ..ingest import document as doc_in
from ..ingest import epub as epub_in
from ..ingest.document import SUFFIXES as DOC_SUFFIXES
from ..ingest import ocr
from ..ingest import pdf as pdf_in
from ..model import Book
from . import audit as audit_mod
from . import chapters as chapters_mod
from . import decisions as dec
from . import ocrfix
from .clean import clean_book

Progress = Callable[[str, float, str], None]   # (stage, fraction 0..1, message)


@dataclass
class PrepOptions:
    review_rounds: int = 3
    redecide: bool = False
    force_ocr: bool = False
    ocr_engine: str = "vision"
    note: str = ""            # the listener's note from the review stop


def _noop(stage, frac, msg):
    pass


def _save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------------
# ingest
# ----------------------------------------------------------------------------

def ingest(src: Path, work: Path, opts: PrepOptions, qc: dict, say: Progress):
    """Returns (kind, dossier, context) where context holds what build() needs."""
    if src.suffix.lower() in DOC_SUFFIXES:
        say("Reading", 0.0, f"Converting the {src.suffix[1:].upper()} file")
        raw = doc_in.read_document(src, work)
    if src.suffix.lower() == ".epub" or src.suffix.lower() in DOC_SUFFIXES:
        if src.suffix.lower() == ".epub":
            say("Reading", 0.0, "Reading the EPUB")
            raw = epub_in.read_epub(src, work)
        raw.save(work / "raw.json")
        a = audit_mod.audit(raw)
        d = dec.epub_dossier(raw, a)
        qc["sections_before"] = [(n, s.label or s.heading or s.title, s.words) for n, s in enumerate(raw.sections, 1)]
        return "epub", d, {"raw": raw}
    need, with_text, total = ocr.needs_ocr(src)
    qc["pages_total"] = total
    if need or opts.force_ocr:
        engine = opts.ocr_engine if ocr.ocr_available(opts.ocr_engine) else ocr.default_engine()
        say("OCR", 0.0, f"Recognising text on {total} pages ({engine})")
        data = ocr.ocr_words(src, work / "ocr.json", engine=engine, force=opts.force_ocr,
                             progress=lambda i, n: say("OCR", i / max(1, n), f"OCR page {i} of {n}"))
        ps = pdf_in.PageSet.from_ocr(src, data)
        qc["events"].append(f"OCR: {data['stats']['pages_ocrd']} of {total} pages recognised with {engine}"
                            + (" (forced)" if opts.force_ocr else f" ({with_text} pages had a text layer)"))
        qc["ocr"] = data["stats"]
    else:
        ps = pdf_in.PageSet.from_pdf(src)
        qc["events"].append(f"OCR: not needed ({with_text} of {total} pages have a text layer)")
    say("Reading", 0.5, "Analysing page layout")
    d = pdf_in.dossier(ps)
    bad, junky, text_pages = ocr.needs_reocr(d)
    if bad and not (need or opts.force_ocr):
        qc["warnings"].append(f"{junky} of {text_pages} text pages looked garbled; ran OCR over the whole book.")
        engine = ocr.default_engine()
        data = ocr.ocr_words(src, work / "ocr.json", engine=engine, force=True,
                             progress=lambda i, n: say("OCR", i / max(1, n), f"OCR page {i} of {n}"))
        ps = pdf_in.PageSet.from_ocr(src, data)
        d = pdf_in.dossier(ps)
        qc["ocr"] = data["stats"]
    if ps.spreads:
        qc["events"].append(f"Two-page spreads split into single pages: {ps.spreads}")
    return "pdf", d, {"pageset": ps}


# ----------------------------------------------------------------------------
# build: apply decisions and measure
# ----------------------------------------------------------------------------

def build(kind: str, ctx: dict, decisions: dict, backend, work: Path, say: Progress) -> tuple[Book, dict]:
    r: dict = {"warnings": [], "events": []}
    flags, reps = decisions.get("flags") or [], decisions.get("replacements") or []
    if kind == "pdf":
        ps = ctx["pageset"]
        say("Building", 0.1, "Converting kept pages")
        book, info = pdf_in.convert(ps, decisions.get("pages"), decisions.get("chapters"),
                                    decisions.get("title") or None, decisions.get("author") or None)
        r["build"] = info
        n = ps.page_count
        keep = pdf_in.parse_pages(decisions.get("pages"), n)
        r["pages_kept"] = pdf_in.pages_to_str([p + 1 for p in keep])
        r["pages_cut"] = pdf_in.pages_to_str(sorted(set(range(1, n + 1)) - {p + 1 for p in keep}))
        for m in info.get("chapter_matches", []):
            if not m.get("found"):
                r["warnings"].append(f"Chapter heading not found on page {m['page']} for \"{m['title']}\"; "
                                     "the chapter starts at the top of that page instead.")
    else:
        raw: Book = ctx["raw"]
        book = raw.copy()
        drop = set(decisions.get("drop") or [])
        titles = decisions.get("titles") or {}
        kept = []
        for n, s in enumerate(book.sections, 1):
            if n in drop:
                continue
            if str(n) in titles:
                s.title = titles[str(n)]
            kept.append(s)
        book.sections = kept
        r["dropped"] = sorted(drop)
        if decisions.get("title"):
            book.title = decisions["title"]
        if decisions.get("author"):
            book.author = decisions["author"]
    book = clean_book(book, flags, reps)

    say("Building", 0.3, "Auditing the text")
    r["audit_before"] = audit_mod.audit(book)
    if not isinstance(backend, NoAI) and r["audit_before"]["issues"] > 0:
        heavy = r["audit_before"]["verdict"] == "HEAVY"
        say("Correcting", 0.0, "Heavy damage: sending every paragraph to the AI" if heavy else
            f"Sending {r['audit_before']['issues']} flagged passages to the AI")
        try:
            book, fix = ocrfix.correct(book, backend, work, everything=heavy,
                                       progress=lambda i, n: say("Correcting", i / max(1, n), f"AI request {i} of {n}"))
            if heavy:
                r["events"].append("Heavy damage: every paragraph was sent for AI correction, not only flagged ones")
            r["ocr_fix"] = fix
            if fix["failed"]:
                r["warnings"].append(f"{fix['failed']} AI correction request(s) failed ({fix['first_error'][:120]}); "
                                     "run the book again later to finish them (finished work is kept).")
        except Exception as e:
            r["warnings"].append(f"AI correction failed ({e}); continuing without it.")
    elif isinstance(backend, NoAI):
        r["events"].append("AI correction: skipped (no AI)")

    r["chapters"] = chapters_mod.prepare_chapters(book)
    r["audit_after"] = audit_mod.audit(book)
    r["validation"] = validate(book)
    r["sections_after"] = [{"n": n, "title": s.title, "words": s.words, "opening": " ".join(s.text.split())[:160]}
                           for n, s in enumerate(book.sections, 1)]
    r["debris"] = find_debris(book)
    r["unmatched"] = len([m for m in (r.get("build") or {}).get("chapter_matches", []) if not m.get("found")])
    r["tiny"] = len([s for s in r["sections_after"] if s["words"] < 40
                     and not re.match(r"(part|book|volume|section)\b", s["title"].strip(), re.I)])
    r["words"] = book.words
    r["score"] = (r["audit_after"]["issues"] + 3 * len(r["debris"]) + 5 * r["unmatched"] + 5 * r["tiny"]
                  + 20 * len(r["validation"]))
    return book, r


def validate(book: Book) -> list[str]:
    problems = []
    if not book.sections:
        problems.append("the book has no sections")
    elif book.words < 50:
        problems.append("the book has almost no text")
    for n, s in enumerate(book.sections, 1):
        if not s.title.strip():
            problems.append(f"section {n} has no title")
        if any("\n" in p for p in s.paragraphs):
            problems.append(f"section {n} has a line break inside a paragraph")
    return problems


DEBRIS_NUM_CAPS = re.compile(r"(?<![\w,])\d{1,4}\s+(?:[A-Z][A-Z'’.:,|/-]{1,}\s*){1,4}(?=\s*$|\s+[a-z(“\"])")
DEBRIS_TAIL_CAPS = re.compile(r"[a-z,;]\s+((?:[A-Z][A-Z'’.:|/-]{3,}\s*){1,3})$")


def find_debris(book: Book) -> list[dict]:
    """Leftover page furniture: 'page number + CAPS' fragments and CAPS runs stuck to paragraph ends."""
    hits = []
    for n, s in enumerate(book.sections, 1):
        for para in s.paragraphs:
            for m in DEBRIS_NUM_CAPS.finditer(para):
                hits.append({"n": n, "kind": "page number + capitals", "text": m.group(0).strip(),
                             "context": para[max(0, m.start() - 60): m.end() + 40]})
            m = DEBRIS_TAIL_CAPS.search(para)
            if m and len(para) > 80:
                hits.append({"n": n, "kind": "capitals at paragraph end", "text": m.group(1).strip(),
                             "context": para[max(0, m.start() - 60):]})
    return hits[:60]


def review_packet(kind: str, decisions: dict, book: Book, r: dict, rnd: int, rounds: int, work: Path) -> str:
    L = [f"ROUND {rnd} OF {rounds}. Book type: {kind.upper()}. Words: {r['words']:,}. "
         f"Problem score: {r['score']} (lower is better)."]
    L.append("CURRENT DECISIONS: " + dec.dumps(decisions)[:12000])
    L.append("SECTIONS AS BUILT (number, spoken title, words, opening ... ending):")
    for n, s in enumerate(book.sections, 1):
        flat = " ".join(s.text.split())
        L.append(f"#{n} \"{s.title[:80]}\" w={s.words} :: {flat[:200]} ... {flat[-120:] if len(flat) > 320 else ''}")
    if r["debris"]:
        L.append("POSSIBLE LEFTOVER PAGE FURNITURE (section, text, context):")
        for h in r["debris"][:40]:
            L.append(f"#{h['n']} [{h['kind']}] \"{h['text']}\" :: {h['context']}")
    fl = audit_mod.flag_contexts(book)
    if fl:
        L.append("AUDIT FLAGS (section, reason, token, context):")
        for f in fl:
            L.append(f"#{f['n']} [{f['reason']}] \"{f['token']}\" :: {f['context']}")
    miss = [m for m in (r.get("build") or {}).get("chapter_matches", []) if not m.get("found")]
    if miss:
        L.append("CHAPTER HEADINGS NOT FOUND (placed at page top): " + "; ".join(f"p{m['page']} {m['title']}" for m in miss))
    rej = ocrfix.rejected_lines(work)
    if rej:
        L.append("OCR CORRECTIONS REJECTED BY THE SAFETY CHECK (for context; fix with replacements if needed):")
        L += rej
    if r["validation"]:
        L.append("VALIDATION PROBLEMS: " + "; ".join(r["validation"][:10]))
    return "\n".join(L)


def corpus_of(kind: str, ctx: dict, book: Book) -> str:
    """Text a replacement may match: the finished book plus the book as first read (before cleanup)."""
    parts = [s.text for s in book.sections]
    if kind == "epub":
        parts += [s.text for s in ctx["raw"].sections]
    else:
        parts += [" ".join(w[4] for w in p.words) for p in ctx["pageset"].pages]
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# the whole prepare stage
# ----------------------------------------------------------------------------

def prepare(src: Path, work: Path, backend, opts: PrepOptions, say: Progress = _noop) -> tuple[Book, dict]:
    src = Path(src)
    qc = {"source": str(src), "type": src.suffix.lower()[1:], "events": [], "warnings": [],
          "started": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "ai": backend.label}
    use_ai = not isinstance(backend, NoAI)
    if use_ai:
        ok, why = backend.available()
        if not ok:
            qc["warnings"].append(f"AI not available ({why}); used local heuristics instead.")
            backend, use_ai = NoAI(), False
    kind, dossier, ctx = ingest(src, work, opts, qc, say)
    _save_json(work / "dossier.json", dossier)

    decisions_path = work / "decisions.json"
    if decisions_path.exists() and not opts.redecide:
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        qc["events"].append("Decisions: reused the saved decisions.json")
    else:
        if use_ai:
            say("Deciding", 0.0, "Asking the AI where chapters start and what to cut")
            try:
                raw = ask_json(backend, dec.prompt_for(kind, dossier), purpose="decisions")
                raw["_source"] = "ai"
            except Exception as e:
                qc["warnings"].append(f"AI decision step failed ({e}); used local heuristics instead.")
                raw = dec.heuristic(kind, dossier)
                raw["_source"] = "heuristic"
        else:
            raw = dec.heuristic(kind, dossier)
            raw["_source"] = "heuristic"
        decisions, warns = dec.sanitise(kind, raw, dossier)
        qc["warnings"] += warns
        _save_json(decisions_path, decisions)

    book, r = build(kind, ctx, decisions, backend, work, say)

    history, stop = [], "review rounds turned off" if not opts.review_rounds else ""
    rounds = opts.review_rounds if use_ai else 0
    if not use_ai:
        stop = "no AI"
    note = opts.note.strip()
    if note and use_ai:
        rounds = max(rounds, 1)
    for rnd in range(1, rounds + 1):
        say("Reviewing", (rnd - 1) / rounds, f"Review round {rnd} of {rounds}")
        prompt = (dec.NOTE_PREAMBLE.format(note=note) if note and rnd == 1 else "") + dec.REVIEW_PROMPT \
            + review_packet(kind, decisions, book, r, rnd, rounds, work)
        try:
            resp = ask_json(backend, prompt, purpose="review")
        except (AIError, Exception) as e:
            history.append({"round": rnd, "outcome": "error", "reasons": [str(e)[:300]], "applied": [], "ignored": []})
            stop = "the review call failed"
            break
        patch = resp.get("patch") or {}
        reasons = [str(x) for x in (resp.get("reasons") or [])][:12]
        if not patch or not any(patch.values()):
            history.append({"round": rnd, "outcome": "nothing to fix", "reasons": reasons, "applied": [], "ignored": []})
            stop = "the AI found nothing further to fix"
            break
        new_dec, applied, ignored = dec.apply_patch(kind, decisions, patch, corpus_of(kind, ctx, book), dossier)
        if not applied:
            history.append({"round": rnd, "outcome": "no usable changes", "reasons": reasons, "applied": [],
                            "ignored": ignored})
            stop = "the AI's proposed changes could not be applied"
            break
        say("Reviewing", (rnd - 0.5) / rounds, f"Rebuilding with {len(applied)} correction(s)")
        new_book, new_r = build(kind, ctx, new_dec, backend, work, say)
        # the listener's note may legitimately raise the score (for example by restoring cut text)
        worse = (new_r["words"] < r["words"] * 0.93 or len(new_r["validation"]) > len(r["validation"])
                 or (new_r["score"] > r["score"] and not (note and rnd == 1)))
        entry = {"round": rnd, "reasons": reasons, "applied": applied, "ignored": ignored,
                 "score": [r["score"], new_r["score"]], "words": [r["words"], new_r["words"]]}
        if worse:
            entry["outcome"] = "reverted (made things worse)"
            history.append(entry)
            stop = "a round made the book worse, so it was undone"
            break
        decisions, book, r = new_dec, new_book, new_r
        _save_json(decisions_path, decisions)
        entry["outcome"] = "accepted"
        history.append(entry)
        if resp.get("done"):
            stop = "the AI reported the book clean after this round"
            break
        if rnd == rounds:
            stop = f"reached the {rounds}-round limit"

    qc.update({k: v for k, v in r.items() if k not in ("warnings", "events")})
    qc["warnings"] += r["warnings"]
    qc["events"] += r["events"]
    qc["decisions"] = decisions
    qc["review"] = history
    qc["review_stop"] = stop
    qc["note"] = note
    qc["usage"] = {"calls": backend.usage.calls, "input_tokens": backend.usage.input_tokens,
                   "output_tokens": backend.usage.output_tokens, "cost": round(backend.usage.cost, 4)}
    if book.cover is None and kind == "pdf":
        book.cover = pdf_in.cover_image(src, work / "cover.png")
    elif kind == "epub" and ctx["raw"].cover:
        book.cover = ctx["raw"].cover
    book.source, book.kind = str(src), kind
    qc["title"], qc["author"] = book.title, book.author
    book.save(work / "book.json")
    _save_json(work / "qc.json", qc)
    return book, qc


def reset(work: Path) -> None:
    """Forget everything prepared for a book (keeps nothing but the folder)."""
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
