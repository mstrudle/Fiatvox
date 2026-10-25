"""The `fiatvox` command. With no arguments it opens the app; subcommands run parts of it directly."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, paths
from . import platform as fv_platform


def book_id(src: Path) -> str:
    """Stable per source file: a readable slug plus a hash of the full path (same name in two folders differs)."""
    slug = re.sub(r"[^a-z0-9]+", "-", src.stem.lower()).strip("-")[:40] or "book"
    return f"{slug}-{hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]}"


def keep_awake() -> subprocess.Popen | None:
    """Keep the Mac awake while this process runs."""
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None


def say(msg: str) -> None:
    print(f"[fiatvox] {msg}", flush=True)


def _fmt_eta(sec) -> str:
    if sec is None:
        return "estimating"
    sec = int(sec)
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s"


def cmd_make(a) -> int:
    from .ai import get_backend
    from .finish import books, report
    from .narrate.assemble import safe_filename
    from .narrate.kokoro_engine import resolve_voice
    from .narrate.narrator import Cancelled, narrate
    from .prepare.pipeline import PrepOptions, prepare, reset
    from .settings import Settings

    st = Settings.load()
    src = Path(a.book).expanduser().resolve()
    from .ingest.document import SUFFIXES as DOC_SUFFIXES
    if not src.exists() or src.suffix.lower() not in (".epub", ".pdf") + DOC_SUFFIXES:
        say(f"Not a book file Fiat Vox can read (EPUB, PDF, RTF, DOCX, ODT or text): {src}")
        return 2
    voice = resolve_voice(a.voice or st.default_voice)
    out_dir = Path(a.out or st.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = paths.book_dir(book_id(src))
    if a.fresh:
        reset(work)
    provider = "none" if a.no_ai else (a.provider or st.ai_provider)
    backend = get_backend(provider, a.model if a.model is not None else st.ai_model)
    keep_awake()

    last = [0.0]

    def progress(stage, frac, msg):
        now = time.monotonic()
        if now - last[0] > 2 or frac in (0.0, 1.0):
            say(f"{stage}: {msg}")
            last[0] = now

    t0 = time.monotonic()
    from . import troubleshoot as ts
    opts = PrepOptions(review_rounds=st.review_rounds if a.review_rounds is None else a.review_rounds,
                       redecide=a.redecide, force_ocr=a.force_ocr, ocr_engine=a.ocr or st.ocr_engine,
                       note=a.note or "")
    kind = "pdf" if src.suffix.lower() == ".pdf" else "text"
    incident = ts.Incident(book=work.name, source=str(src))
    state = {"stage": "preparing", "reprepare": False, "book": None, "qc": None}

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

    def do_prepare():
        state["stage"] = "preparing"
        state["book"], state["qc"] = prepare(src, work, backend, opts, progress)
        state["reprepare"] = False
        return state["book"]

    never = (KeyboardInterrupt, Cancelled)
    try:
        say(f"Preparing {src.name} (AI: {backend.label})")
        ts.run_with_recovery("preparing the book", do_prepare, apply, backend, incident, context, say, never)
    except ts.GaveUp as e:
        say(f"Could not prepare the book: {e}")
        say(f"Error report: {e.report}")
        return 1
    book, qc = state["book"], state["qc"]
    for w in qc["warnings"]:
        say(f"Warning: {w}")
    say(f"Prepared: {len(book.sections)} chapters, {book.words:,} words in {time.monotonic() - t0:.0f}s")
    if a.prepare_only:
        _close_incident(incident, context, say)
        say(f"Work folder: {work}")
        return 0

    sections = None
    if a.sections:
        sections = sorted({int(x) - 1 for part in a.sections.split(",") for x in _range(part)})

    def nprog(ev):
        if ev.get("stage") == "assembling":
            if ev.get("done") is None:
                say("Assembling the audiobook")
            return
        now = time.monotonic()
        if now - last[0] > 5:
            pct = 100 * ev["done"] / max(1, ev["total"])
            say(f"Narrating chapter {ev['chapter']} of {ev['chapters']} ({pct:.0f}%), {_fmt_eta(ev['eta'])} left")
            last[0] = now

    def do_narrate():
        if state["reprepare"]:
            do_prepare()
        state["stage"] = "narrating"
        b = state["book"]
        name = safe_filename(f"{b.author} - {b.title}" if b.author else b.title or src.stem)
        state["out"] = out_dir / f"{name}.m4b"
        say(f"Narrating with {voice} to {state['out']}")
        return narrate(b, voice, state["out"], st.silences(), st.bitrate_kbps, progress=nprog, sections=sections)

    try:
        nar = ts.run_with_recovery("narrating the book", do_narrate, apply, backend, incident, context, say, never)
    except (KeyboardInterrupt, Cancelled):
        say("Stopped. Everything narrated so far is kept; run the same command to resume.")
        return 130
    except ts.GaveUp as e:
        say(f"Could not narrate the book: {e}")
        say(f"Error report: {e.report}")
        return 1
    _close_incident(incident, context, say)
    book, qc, out = state["book"], state["qc"], state["out"]
    say(f"Done: {out.name}, {len(nar['chapters'])} chapters, {nar['duration'] / 3600:.2f} h of audio")
    if st.qc_report and not a.no_report:
        rp = out.with_name(out.stem + " - QC report.md")
        report.write(rp, qc, nar)
        say(f"QC report: {rp}")
    if st.import_to_books and not a.no_import:
        ok, err = books.import_to_books(out)
        say("Imported into Apple Books" if ok else f"Could not import into Books: {err}")
    if st.notify:
        books.notify("Fiat Vox", f"{book.title or src.stem} is ready.")
    return 0


def _close_incident(incident, context, say) -> None:
    """A failure that a remedy fixed still gets a report, so the cause can be fixed in the code."""
    if incident.attempts:
        incident.outcome = "recovered"
        say(f"Recovered after {len(incident.attempts)} failure(s). Report: {incident.write(context())}")


def cmd_errors(a) -> int:
    from .troubleshoot import list_reports
    reports = list_reports(a.limit)
    if not reports:
        print("No error reports.")
        return 0
    for r in reports:
        print(f"{r['when']}  {r['outcome']:<32}  {r['book']}")
        print(f"    {r['first_error'][:110]}")
        print(f"    {r['file']}")
    return 0


def _range(part: str):
    part = part.strip()
    if "-" in part:
        a, b = part.split("-", 1)
        return range(int(a), int(b) + 1)
    return [int(part)]


def cmd_signin(a) -> int:
    """Choose an AI route and sign in, in the terminal (the app's setup screen does the same)."""
    import getpass
    import webbrowser

    from .ai import get_backend, pi_backend
    from .ai.setup import CLAUDE_NOTE, choices, pick_model, save_direct_key
    from .settings import Settings

    st = Settings.load()
    print("\nHow should Fiat Vox reach an AI model? It is used to decide chapters and fix scanning errors.\n")
    print(CLAUDE_NOTE + "\n")
    opts = choices()
    for i, c in enumerate(opts, 1):
        print(f"  {i:>2}. {c.label}")
    try:
        n = int(input("\nNumber: ").strip())
        c = opts[n - 1]
    except (ValueError, IndexError):
        print("No change.")
        return 1
    if c.kind == "none":
        st.ai_provider, st.ai_model, st.setup_done = "none", "", True
        st.save()
        print("AI turned off. Fiat Vox will use local heuristics.")
        return 0
    if c.kind == "claude-code":
        st.ai_provider, st.ai_model = "claude-code", "sonnet"
    elif c.kind == "direct":
        key = getpass.getpass(f"Paste your {c.label.split(' key')[0]} key (input hidden): ").strip()
        if not key:
            print("No key entered; no change.")
            return 1
        save_direct_key(c.provider, key)
        st.ai_provider, st.ai_model = c.provider, ""
    else:
        def on_event(ev):
            kind = ev.get("event")
            if kind == "auth_url":
                print(f"\nOpening your browser to sign in:\n  {ev['url']}")
                if ev.get("instructions"):
                    print(ev["instructions"])
                webbrowser.open(ev["url"])
            elif kind == "device_code":
                print(f"\nVisit {ev['verificationUri']} and enter the code: {ev['userCode']}")
                webbrowser.open(ev["verificationUri"])
            elif kind in ("info", "progress"):
                print(ev.get("message", ""))
            elif kind == "prompt_withdrawn":
                print("(Signed in through the browser; no need to paste anything.)")
            elif kind == "prompt":
                if ev.get("type") == "select":
                    for i, o in enumerate(ev["options"], 1):
                        print(f"  {i}. {o['label']}" + (f": {o['description']}" if o.get("description") else ""))
                    try:
                        return ev["options"][int(input(ev["message"] + " ").strip()) - 1]["id"]
                    except (ValueError, IndexError):
                        return None
                if ev.get("type") == "secret":
                    return getpass.getpass(ev["message"] + " (input hidden): ")
                if ev.get("type") == "manual_code":
                    return _wait_for_code(ev["message"], ev["_withdrawn"])
                return input(ev["message"] + " ")
            return None

        pi_backend.login(c.provider, "oauth" if c.kind == "pi-oauth" else "api_key", on_event)
        model = pick_model(c.provider, pi_backend.models())
        st.ai_provider, st.ai_model = "pi", model
        print(f"\nSigned in. Model: {model or 'Pi default'}")
    st.setup_done = True
    st.save()  # saved before the test, so interrupting the test never loses the choice
    print(f"Saved: Fiat Vox will use {get_backend(st.ai_provider, st.ai_model).label}.")
    backend = get_backend(st.ai_provider, st.ai_model)
    ok, why = backend.available()
    if not ok:
        print(why)
        return 1
    print("Checking it works (this can take up to a minute)...")
    try:
        reply = backend.complete('Reply with exactly this JSON: {"ok": true}', purpose="test", timeout=120)
        print(f"It works. Reply from {backend.label}: {reply.strip()[:60]}")
    except Exception as e:
        print(f"The choice is saved, but a test call failed: {e}")
        return 1
    return 0


def _wait_for_code(message: str, withdrawn) -> str | None:
    """Wait for a pasted code, but stop waiting as soon as the browser completes the sign-in."""
    import select
    print(message + " ", end="", flush=True)
    while True:
        if withdrawn():
            print()
            return None
        ready, _, _ = select.select([sys.stdin], [], [], 0.5)
        if ready:
            return sys.stdin.readline().strip()


def cmd_voices(a) -> int:
    """List the voices; play a sample; or make every sample now."""
    from . import voices
    from .narrate.kokoro_engine import VOICES
    if a.make_samples:
        made = voices.make_samples(force=a.force,
                                   progress=lambda i, n, v: print(f"Making sample {i} of {n}: {v}", flush=True))
        print(f"{len(made)} sample(s) made; the others were already there." if made else "All samples are ready.")
        return 0
    if a.play:
        v = a.play
        print(f"{v}: {voices.describe(v)}")
        voices.play(v, wait=True)
        return 0
    for v in VOICES:
        mark = "" if voices.has_sample(v) else "  (no sample yet)"
        print(f"{v:<12} {voices.describe(v)}{mark}")
    print("\nPlay one with:  fiatvox voices --play george")
    return 0


def main(argv=None) -> int:
    fv_platform.check()
    ap = argparse.ArgumentParser(prog="fiatvox", description="Turn EPUB and PDF books into audiobooks.")
    ap.add_argument("--version", action="version", version=f"Fiat Vox {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("make", help="prepare and narrate one book")
    p.add_argument("book")
    p.add_argument("--voice", help="voice, for example bm_george or af_heart (see `fiatvox voices`)")
    p.add_argument("--out", help="output folder (default from settings)")
    p.add_argument("--no-ai", action="store_true", help="local heuristics only")
    p.add_argument("--provider", help="pi, anthropic, openai, gemini, openrouter or none")
    p.add_argument("--model", help="AI model (Pi: provider/model)")
    p.add_argument("--review-rounds", type=int)
    p.add_argument("--note", help="a note for the AI about what to fix")
    p.add_argument("--force-ocr", action="store_true")
    p.add_argument("--ocr", choices=["vision", "tesseract"])
    p.add_argument("--redecide", action="store_true", help="ask the AI again even if decisions exist")
    p.add_argument("--fresh", action="store_true", help="forget everything prepared for this book")
    p.add_argument("--prepare-only", action="store_true", help="stop before narration")
    p.add_argument("--sections", help="narrate only these chapters, for example 1-3,7")
    p.add_argument("--no-import", action="store_true", help="do not import into Apple Books")
    p.add_argument("--no-report", action="store_true", help="do not write the QC report")
    p.set_defaults(func=cmd_make)

    p = sub.add_parser("errors", help="list error reports from failed runs")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_errors)

    p = sub.add_parser("signin", help="choose an AI provider and sign in")
    p.set_defaults(func=cmd_signin)

    p = sub.add_parser("voices", help="list the voices, play a sample, or make all samples")
    p.add_argument("--play", metavar="VOICE", help="play this voice's sample, for example george or bf_emma")
    p.add_argument("--make-samples", action="store_true", help="make every voice's sample now")
    p.add_argument("--force", action="store_true", help="with --make-samples: make them again")
    p.set_defaults(func=cmd_voices)

    a = ap.parse_args(argv)
    if not a.cmd:
        from .tui.app import run
        return run()
    try:
        return a.func(a) or 0
    except KeyboardInterrupt:
        say("Stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
