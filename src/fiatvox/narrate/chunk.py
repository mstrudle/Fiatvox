"""Turn a book's sections into the units Kokoro speaks, with the silences between them.

One unit is one paragraph (or one spoken title). Kokoro splits a long paragraph into its own chunks at
punctuation; Fiat Vox never splits text at ". " itself, which is what put pauses into "U.S. Army" before.
The spoken copy is made here: normalisation, then spelling substitutions, then phoneme overrides, so the audit
and the chapter names never see any of it.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..model import Book
from . import normalise as norm
from . import pronounce

OVERRIDE = re.compile(r"\[[^\[\]\s]+\]\(/[^/\s]+/\)")
SPEAKABLE = re.compile(r"[A-Za-z0-9\u00c0-\u024f]")
SCENE_BREAK = 1.2  # seconds of silence for a scene break


@dataclass
class Silences:
    before_title: float = 1.5
    after_title: float = 0.8
    paragraph: float = 0.4
    chapter_end: float = 1.0


@dataclass
class Unit:
    text: str              # exactly what Kokoro receives
    kind: str              # "title" or "para"
    pause_before: float = 0.0
    pause_after: float = 0.0


@dataclass
class SpokenChapter:
    title: str             # chapter name for the .m4b, as written
    units: list[Unit] = field(default_factory=list)


@dataclass
class Speaker:
    """Everything needed to make the spoken copy for one accent."""
    accent: str
    lexicon: dict[str, str]
    spelling: list
    regnal: set[str] = field(default_factory=set)
    log_spoken: Counter = field(default_factory=Counter)
    log_spelling: Counter = field(default_factory=Counter)
    log_phonemes: Counter = field(default_factory=Counter)

    def __post_init__(self):
        self._pat = pronounce.override_pattern(self.lexicon)
        self._rules = pronounce.spelling_rules(self.spelling)

    @classmethod
    def for_voice(cls, voice: str, book: Book | None = None) -> "Speaker":
        accent = pronounce.accent_of(voice)
        regnal = set()
        if book is not None:
            for s in book.sections:
                regnal |= norm.regnal_names(s.text)
        return cls(accent, pronounce.load_phonemes(accent), pronounce.load_spelling(), regnal)

    def speak(self, text: str) -> str:
        text = one_line(text)
        text = norm.normalise(text, self.log_spoken, self.regnal)
        text = pronounce.apply_spelling(text, self._rules, self.log_spelling)
        text = pronounce.apply_overrides(text, self.lexicon, self.log_phonemes, self._pat)
        return text


def one_line(text: str) -> str:
    """Collapse all whitespace, including newlines, to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def spoken_title(title: str) -> str:
    t = one_line(title)
    return t if t[-1:] in ".?!" else t + "."


def chapters(book: Book, speaker: Speaker, silences: Silences) -> list[SpokenChapter]:
    out = []
    for sec in book.sections:
        ch = SpokenChapter(title=sec.title)
        ch.units.append(Unit(speaker.speak(spoken_title(sec.title)), "title",
                             silences.before_title, silences.after_title))
        paras = [p for p in sec.paragraphs if p.strip()]
        for i, p in enumerate(paras):
            last = i == len(paras) - 1
            if not SPEAKABLE.search(p):
                # a scene break ("* * *") or ornament: nothing to say, so a longer pause instead
                if ch.units:
                    ch.units[-1].pause_after = max(ch.units[-1].pause_after, SCENE_BREAK)
                continue
            ch.units.append(Unit(speaker.speak(p), "para", 0.0,
                                 silences.chapter_end if last else silences.paragraph))
        if ch.units and ch.units[-1].kind == "para":
            ch.units[-1].pause_after = max(ch.units[-1].pause_after, silences.chapter_end)
        out.append(ch)
    return out


def check_unit(text: str) -> list[str]:
    """Problems that would make Kokoro read markup aloud."""
    problems = []
    if "\n" in text:
        problems.append("newline inside a unit")
    stripped = OVERRIDE.sub("", text)
    if re.search(r"\]\(/|\[[^\]]*\]\(", stripped):
        problems.append("broken pronunciation override")
    return problems
