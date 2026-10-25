"""Narrate a prepared book: spoken copy, Kokoro per unit, cache, silences, then the .m4b.

Cancel is checked between units: the unit in progress finishes and is cached, so nothing is lost.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..model import Book
from . import cache
from .assemble import ChapterAudio, silence, write_m4b
from .chunk import Silences, Speaker, SpokenChapter, Unit, chapters as spoken_chapters, check_unit
from .kokoro_engine import SAMPLE_RATE, Engine

_ENGINE: Engine | None = None


def engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine()
    return _ENGINE


class Cancelled(Exception):
    pass


@dataclass
class Plan:
    voice: str
    chapters: list[SpokenChapter]
    speaker: Speaker
    keys: list[list[str]] = field(default_factory=list)

    @property
    def units(self) -> int:
        return sum(len(c.units) for c in self.chapters)

    def chars(self, only_missing: bool = False) -> int:
        n = 0
        for ch, ks in zip(self.chapters, self.keys):
            for u, k in zip(ch.units, ks):
                if not only_missing or not cache.has(k):
                    n += len(u.text)
        return n

    def problems(self) -> list[str]:
        out = []
        for ch in self.chapters:
            for u in ch.units:
                for p in check_unit(u.text):
                    out.append(f"{ch.title[:40]}: {p}: {u.text[:80]}")
        return out


def plan(book: Book, voice: str, silences: Silences, sections: list[int] | None = None) -> Plan:
    speaker = Speaker.for_voice(voice, book)
    chs = spoken_chapters(book, speaker, silences)
    if sections is not None:
        chs = [chs[i] for i in sections]
    p = Plan(voice, chs, speaker)
    p.keys = [[cache.key(u.text, voice) for u in ch.units] for ch in chs]
    return p


def unit_audio(u: Unit, k: str, voice: str):
    a = cache.get(k)
    if a is None:
        a = engine().synth(u.text, voice)
        cache.put(k, a)
    return a


def render(p: Plan, progress: Callable[[dict], None] | None = None,
           cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Make sure every unit is in the cache. Reports chars done and a speed estimate for time remaining."""
    total = p.chars()
    done = p.chars() - p.chars(only_missing=True)
    t0, synth_chars = time.monotonic(), 0
    for ci, (ch, ks) in enumerate(zip(p.chapters, p.keys)):
        for u, k in zip(ch.units, ks):
            if cache.has(k):
                continue
            if cancelled():
                raise Cancelled()
            a = engine().synth(u.text, p.voice)
            cache.put(k, a)
            done += len(u.text)
            synth_chars += len(u.text)
            if progress:
                rate = synth_chars / max(1e-6, time.monotonic() - t0)
                progress({"chapter": ci + 1, "chapters": len(p.chapters), "title": ch.title, "done": done,
                          "total": total, "eta": (total - done) / rate if rate else None,
                          "audio_seconds": len(a) / SAMPLE_RATE})
    return {"chars": total, "synthesised_chars": synth_chars, "seconds": time.monotonic() - t0}


def chapter_audio(p: Plan) -> list[ChapterAudio]:
    """Lazy chapter streams for assembly; lengths are computed from the cached clips."""
    out = []
    for ch, ks in zip(p.chapters, p.keys):
        n = 0
        for u, k in zip(ch.units, ks):
            f = cache.frames(k)
            if f is None:  # unreadable or missing: record it again
                cache.put(k, engine().synth(u.text, p.voice))
                f = cache.frames(k)
            n += int(round(u.pause_before * SAMPLE_RATE)) + f + int(round(u.pause_after * SAMPLE_RATE))

        def pieces(ch=ch, ks=ks):
            for u, k in zip(ch.units, ks):
                if u.pause_before:
                    yield silence(u.pause_before)
                a = cache.get(k)
                if a is None:
                    raise RuntimeError("a narrated clip went missing from the cache; run the book again")
                yield a
                if u.pause_after:
                    yield silence(u.pause_after)
        out.append(ChapterAudio(ch.title, pieces, n))
    return out


def narrate(book: Book, voice: str, out: Path, silences: Silences, bitrate_kbps: int = 64,
            progress: Callable[[dict], None] | None = None, cancelled: Callable[[], bool] = lambda: False,
            sections: list[int] | None = None) -> dict:
    """Render (resuming from the cache) and assemble. Returns facts for the QC report."""
    p = plan(book, voice, silences, sections)
    probs = p.problems()
    if probs:
        raise RuntimeError("Text would be read with markup aloud: " + "; ".join(probs[:3]))
    stats = render(p, progress, cancelled)
    if progress:
        progress({"stage": "assembling"})
    table = write_m4b(out, book.title or "Audiobook", book.author or "", chapter_audio(p), book.cover, bitrate_kbps,
                      progress=(lambda w, t: progress({"stage": "assembling", "done": w, "total": t}))
                      if progress else None, cancelled=cancelled)
    return {"voice": voice, "file": str(out), "sections": sections if sections is not None else
            list(range(len(book.sections))), "chapters": [(t, a / SAMPLE_RATE, b / SAMPLE_RATE) for t, a, b in table],
            "duration": table[-1][2] / SAMPLE_RATE if table else 0.0, "render": stats,
            "spoken_fixes": sorted(p.speaker.log_spoken.items(), key=lambda kv: (-kv[1], kv[0])),
            "spelling_fixes": sorted(p.speaker.log_spelling.items(), key=lambda kv: (-kv[1], kv[0])),
            "phoneme_fixes": sorted(p.speaker.log_phonemes.items(), key=lambda kv: (-kv[1], kv[0]))}
