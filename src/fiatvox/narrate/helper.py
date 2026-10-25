"""The pronunciation fix helper: from "quasi" said "kwah-zee" to a checked phoneme override, heard before saving.

1. The AI proposes phonemes in misaki's symbol set for the book's accent (or the user types them).
2. Every symbol must be one Kokoro knows, and misaki must pass the override through intact.
3. Kokoro speaks a test sentence with it; the user listens, and compares with the current reading.
4. Saved to the user's list for that accent only on approval.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..ai import ask_json
from . import pronounce
from .g2p import markup_leaks, phonemes, unknown_symbols

TEST_SENTENCE = "Say {word} again, slowly: {word}."

PROMPT = """You write phonemes for the Kokoro text-to-speech model, which uses misaki's phoneme symbols.
Accent: {accent_name} English (misaki {accent_code} set).
Word: "{word}"
How the listener wants it said, in plain respelling: "{respelling}"
How the model says it now, in misaki phonemes: /{current}/

Write the phonemes for the wanted pronunciation, using only symbols that appear in misaki's {accent_name} set,
with the primary stress mark ˈ before the stressed vowel (and ˌ for secondary stress). Keep everything else as
close as possible to the current phonemes. Example for British "quasi" said "kwah-zee": kwˈɑːzi

Return ONLY JSON: {{"phonemes": "...", "explanation": "one short sentence"}}
"""


@dataclass
class Check:
    ok: bool
    problems: list[str]
    phonemes_out: str


def current_reading(word: str, accent: str) -> str:
    return phonemes(word, accent).strip()


def propose(backend, word: str, respelling: str, accent: str) -> tuple[str, str]:
    """(phonemes, explanation) from the AI."""
    d = ask_json(backend, PROMPT.format(accent_name=pronounce.ACCENTS[accent], accent_code=accent, word=word,
                                        respelling=respelling, current=current_reading(word, accent)),
                 purpose="phonemes", timeout=180)
    return str(d.get("phonemes") or "").strip().strip("/"), str(d.get("explanation") or "")


def check(word: str, ph: str, accent: str) -> Check:
    problems = []
    ph = ph.strip().strip("/")
    if not word.strip() or re.search(r"\s", word.strip()):
        problems.append("the word must be a single word")
    if not ph:
        problems.append("no phonemes")
    elif re.search(r"\s", ph):
        problems.append("phonemes must not contain spaces")
    bad = unknown_symbols(ph) if ph else set()
    if bad:
        problems.append("symbols Kokoro does not know: " + " ".join(sorted(bad)))
    out = ""
    if not problems:
        sentence = TEST_SENTENCE.format(word=pronounce.override(word.strip(), ph))
        out = phonemes(sentence, accent)
        if markup_leaks(sentence, accent):
            problems.append("misaki did not accept the override; the symbols would be read aloud")
        elif ph not in out:
            problems.append("misaki changed the phonemes")
    return Check(not problems, problems, out)


def clip(word: str, ph: str | None, voice: str) -> Path:
    """A short clip of the test sentence, with the override (or the current reading when ph is None)."""
    import soundfile as sf

    from .kokoro_engine import SAMPLE_RATE
    from .narrator import engine
    w = pronounce.override(word.strip(), ph.strip().strip("/")) if ph else word.strip()
    audio = engine().synth(TEST_SENTENCE.format(word=w), voice)
    out = paths.ensure(paths.data_dir() / "tmp") / f"pronounce-{'new' if ph else 'current'}.wav"
    sf.write(str(out), audio, SAMPLE_RATE, subtype="PCM_16")
    return out


def play(path: Path, wait: bool = False) -> subprocess.Popen:
    p = subprocess.Popen(["afplay", str(path)])
    if wait:
        p.wait()
    return p


def save(word: str, ph: str, accent: str, respelling: str = "") -> Path:
    return pronounce.save_user_phoneme(accent, word, ph, note=f"{word}: said {respelling}" if respelling else "")
