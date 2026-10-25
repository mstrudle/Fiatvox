"""AI correction of OCR damage in flagged paragraphs. Ported from epub_clean.cmd_ai_fix.

Guards (unchanged): a corrected paragraph must stay within 15% of the original length and at least 0.88 similar.
Every change, accepted or rejected, is logged word by word. Answers are cached by paragraph hash.
"""
from __future__ import annotations

import concurrent.futures
import difflib
import hashlib
import json
import threading
from pathlib import Path

from ..ai import ask_json
from ..model import Book
from .audit import flagged_paragraphs

INSTRUCTIONS = """You are correcting OCR (optical character recognition) errors in text scanned from a printed book, so the text can be read aloud by a speech engine.

Fix ONLY damage introduced by scanning and text extraction:
- misread characters (for example "tbe" -> "the", "rnodern" -> "modern", "l" read as "1", "0" read as "O")
- words wrongly split or joined, including broken ligatures ("fi ction" -> "fiction")
- stray symbols, specks and garbage characters
- hyphens left over from line breaks ("move- ments" -> "movements")
- leftover page numbers, running headers or footnote numbers that interrupt a sentence
- garbled punctuation or quotation marks

Do NOT:
- rephrase, summarise, reorder or modernise anything
- change the author's spelling (British or archaic spellings stay), grammar, style or capitalisation
- change names, numbers, dates or facts unless they are plainly OCR garbage
- add or remove sentences

You will receive a JSON object mapping paragraph ids to text. Return ONLY a JSON object that maps the id of every paragraph you changed to its full corrected text. Leave out paragraphs that need no change. If nothing needs changing, return {}. No commentary, no code fences. Do not use any tools or read any files: everything you need is in this message."""

CHUNK_CHARS = 6000
WORKERS = 3


def acceptable(old: str, new: str) -> tuple[bool, str]:
    if not new.strip():
        return False, "empty"
    lr = len(new) / max(1, len(old))
    if lr < 0.85 or lr > 1.15:
        return False, f"length changed {lr:.2f}x"
    sim = difflib.SequenceMatcher(None, old, new, autojunk=False).ratio()
    if sim < 0.88:
        return False, f"too different ({sim:.2f})"
    return True, f"{sim:.2f}"


def word_diff(old: str, new: str) -> str:
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            out.append(f"`{' '.join(a[i1:i2])}` -> `{' '.join(b[j1:j2])}`")
    return "; ".join(out)


def _h(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def correct(book: Book, backend, work: Path, progress=None, everything: bool = False) -> tuple[Book, dict]:
    """A corrected copy of the book and a summary. Writes ai-log.md and ai-cache.json in `work`.

    everything: send every paragraph, not only flagged ones. Used when the audit rates the damage as heavy (for
    example an EPUB made from a bad scan), where damage the audit cannot see is likely too."""
    if everything:
        items = [(si, pi, p) for si, s in enumerate(book.sections) for pi, p in enumerate(s.paragraphs) if p.strip()]
    else:
        items = flagged_paragraphs(book)
    targets = {f"{si}.{pi}": (si, pi, text) for si, pi, text in items}
    cache_path = work / "ai-cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    todo = {pid: v for pid, v in targets.items() if _h(v[2]) not in cache}
    chunks, cur, size = [], {}, 0
    for pid, v in todo.items():
        if cur and size + len(v[2]) > CHUNK_CHARS:
            chunks.append(cur)
            cur, size = {}, 0
        cur[pid] = v[2]
        size += len(v[2])
    if cur:
        chunks.append(cur)

    lock = threading.Lock()
    failures: list[str] = []
    done = [0]

    def work_one(chunk):
        prompt = INSTRUCTIONS + "\n\nParagraphs:\n" + json.dumps(chunk, ensure_ascii=False)
        try:
            res = ask_json(backend, prompt, purpose="ocr_fix", timeout=600)
        except Exception as e:  # keep going; report at the end
            with lock:
                failures.append(str(e)[:300])
            return
        with lock:
            for pid, text in chunk.items():
                new = res.get(pid, text)
                cache[_h(text)] = new if isinstance(new, str) else text
            done[0] += 1
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            if progress:
                progress(done[0], len(chunks))

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work_one, chunks))

    out = book.copy()
    log = [f"# AI OCR corrections: {book.title}", "",
           "Accepted changes are applied; rejected ones are listed so you can review them.", ""]
    accepted = rejected = 0
    for pid, (si, pi, text) in targets.items():
        new = cache.get(_h(text))
        if new is None or new == text:
            continue
        ok, why = acceptable(text, new)
        if ok:
            out.sections[si].paragraphs[pi] = " ".join(new.split())
            accepted += 1
            log.append(f"- **{pid}** accepted: {word_diff(text, new)}")
        else:
            rejected += 1
            log.append(f"- **{pid}** REJECTED ({why}): {word_diff(text, new)[:400]}")
    (work / "ai-log.md").write_text("\n".join(log) + "\n", encoding="utf-8")
    return out, {"everything": everything, "selected": len(targets), "sent": len(todo), "requests": len(chunks), "accepted": accepted,
                 "rejected": rejected, "failed": len(failures), "first_error": failures[0] if failures else ""}


def rejected_lines(work: Path, limit: int = 10) -> list[str]:
    p = work / "ai-log.md"
    if not p.exists():
        return []
    return [ln.strip()[:300] for ln in p.read_text(encoding="utf-8").splitlines() if "REJECTED" in ln][:limit]
