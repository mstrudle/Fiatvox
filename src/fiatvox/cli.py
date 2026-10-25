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


from .jobs import book_id  # noqa: E402,F401  (kept here for callers and tests)


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
    from . import jobs
    sections = None
    if a.sections:
        sections = sorted({int(x) - 1 for part in a.sections.split(",") for x in _range(part)})
    job = jobs.Job(source=a.book, voice=a.voice or "", out_dir=a.out or "", no_ai=a.no_ai, provider=a.provider,
                   model=a.model, review_rounds=a.review_rounds, note=a.note or "", force_ocr=a.force_ocr,
                   ocr=a.ocr, redecide=a.redecide, fresh=a.fresh, prepare_only=a.prepare_only, sections=sections,
                   import_to_books=False if a.no_import else None, qc_report=False if a.no_report else None)
    keep_awake()
    last = {"t": 0.0}

    def emit(ev: dict) -> None:
        t, now = ev["type"], time.monotonic()
        if t == "log":
            say(ev["msg"])
        elif t == "stage":
            if now - last["t"] > 2 or ev["frac"] in (0.0, 1.0):
                say(f"{ev['stage']}: {ev['msg']}")
                last["t"] = now
        elif t == "prepared":
            say(f"Prepared: {ev['chapters']} chapters, {ev['words']:,} words in {ev['seconds']}s")
            if a.prepare_only:
                say(f"Work folder: {ev['work']}")
        elif t == "narrating":
            if now - last["t"] > 5:
                pct = 100 * ev["done"] / max(1, ev["total"])
                say(f"Narrating chapter {ev['chapter']} of {ev['chapters']} ({pct:.0f}%), {_fmt_eta(ev['eta'])} left")
                last["t"] = now
        elif t == "assembling" and ev.get("done") is None:
            say("Assembling the audiobook")
        elif t == "done":
            say(f"Done: {Path(ev['file']).name}, {ev['chapters']} chapters, {ev['duration'] / 3600:.2f} h of audio")
            if ev["report"]:
                say(f"QC report: {ev['report']}")
            if ev["imported"]:
                say("Imported into Apple Books")
        elif t == "error":
            say(ev["msg"])
            if ev.get("report"):
                say(f"Error report: {ev['report']}")
        elif t == "cancelled":
            say("Stopped. Everything narrated so far is kept; run the same command to resume.")

    r = jobs.run(job, emit)
    return {jobs.OK: 0, jobs.CANCELLED: 130}.get(r.code, 1)


def cmd_worker(a) -> int:
    """Run one job for the app, writing events as JSON lines. SIGTERM or SIGINT cancels cleanly."""
    import signal

    from . import jobs
    job = jobs.Job.from_json(json.loads(Path(a.job).read_text(encoding="utf-8")))
    stop = {"flag": False}

    def on_signal(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    keep_awake()

    def emit(ev: dict) -> None:
        sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    r = jobs.run(job, emit, cancelled=lambda: stop["flag"])
    return r.code


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

    p = sub.add_parser("worker", help=argparse.SUPPRESS)
    p.add_argument("job")
    p.set_defaults(func=cmd_worker)

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
