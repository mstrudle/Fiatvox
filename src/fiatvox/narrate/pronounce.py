"""Pronunciation lists: shared spelling substitutions, and phoneme overrides per accent family.

Shipped lists live in pronunciation/ (american.tsv, british.tsv, spelling.tsv). The user's own additions live in
the data folder (user-american.tsv, user-british.tsv, user-spelling.tsv) and win over the shipped entries.

Overrides use misaki's syntax, [word](/phonemes/), sent straight to Kokoro. The override is one unbroken token: no
whitespace inside it, and paragraphs never contain newlines, so nothing can split it (the old pipeline's bug was a
page layout that wrapped lines inside an override, which made Kokoro read the symbols aloud).
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .. import paths

ACCENTS = {"a": "american", "b": "british"}


def accent_of(voice: str) -> str:
    """'a' (American) or 'b' (British), from the voice prefix."""
    code = voice[:1]
    if code not in ACCENTS:
        raise ValueError(f"Not an English Kokoro voice: {voice}")
    return code


def read_tsv(path: Path) -> list[tuple[str, str]]:
    """find<TAB>replace pairs; blank lines and # comments ignored. Malformed lines raise with the line number."""
    out = []
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" not in line:
            raise ValueError(f"{path} line {n}: expected find<TAB>replace")
        a, b = line.split("\t", 1)
        if a.strip():
            out.append((a.strip(), b.strip()))
    return out


def user_file(kind: str) -> Path:
    """kind: 'american', 'british' or 'spelling'."""
    return paths.user_pronunciation_dir() / f"user-{kind}.tsv"


def load_phonemes(accent: str) -> dict[str, str]:
    """{lowercase word: phonemes without slashes}, shipped list merged with the user's list (user wins)."""
    kind = ACCENTS[accent]
    out: dict[str, str] = {}
    for path in (paths.shipped_pronunciation_dir() / f"{kind}.tsv", user_file(kind)):
        for word, ps in read_tsv(path):
            ps = ps.strip().strip("/")
            if ps:
                out[word.lower()] = ps
    return out


def load_spelling() -> list[tuple[str, str]]:
    """Spelling substitutions, shipped first, then the user's (a user entry for the same text replaces it)."""
    merged: dict[str, str] = {}
    for path in (paths.shipped_pronunciation_dir() / "spelling.tsv", user_file("spelling")):
        for a, b in read_tsv(path):
            merged[a] = b
    return list(merged.items())


def spelling_rules(pairs: list[tuple[str, str]]) -> list[tuple[re.Pattern, str]]:
    """Case-sensitive whole-word rules, in the old pronounce.tsv style."""
    rules = []
    for find, repl in pairs:
        pat = re.compile(r"(?<![\w])" + re.escape(find) + (r"(?![\w])" if find[-1].isalnum() else ""))
        rules.append((pat, repl))
    return rules


def apply_spelling(text: str, rules, log: Counter | None = None) -> str:
    for pat, repl in rules:
        def sub(m, repl=repl):
            if log is not None:
                log[f"{m.group(0)} -> {repl}"] += 1
            return repl
        text = pat.sub(sub, text)
    return text


def override(word: str, phonemes: str) -> str:
    """misaki's inline override for one word."""
    if re.search(r"\s", word) or re.search(r"\s", phonemes):
        raise ValueError(f"override must not contain whitespace: {word!r} {phonemes!r}")
    return f"[{word}](/{phonemes}/)"


def override_pattern(lexicon: dict[str, str]) -> re.Pattern | None:
    if not lexicon:
        return None
    alt = "|".join(re.escape(w) for w in sorted(lexicon, key=len, reverse=True))
    # not inside an existing override ([word] or /phonemes/), and whole words only
    return re.compile(r"(?<![\[\w/])(" + alt + r")(?![\w\]])", re.I)


def apply_overrides(text: str, lexicon: dict[str, str], log: Counter | None = None,
                    pattern: re.Pattern | None = None) -> str:
    """Wrap every listed word in the override syntax, keeping the word's own capitalisation."""
    pat = pattern or override_pattern(lexicon)
    if pat is None:
        return text

    def sub(m):
        ps = lexicon[m.group(1).lower()]
        if log is not None:
            log[f"{m.group(1)} -> /{ps}/"] += 1
        return override(m.group(1), ps)
    return pat.sub(sub, text)


def save_user_phoneme(accent: str, word: str, phonemes: str, note: str = "") -> Path:
    """Add or replace one word in the user's list for an accent."""
    path = user_file(ACCENTS[accent])
    word, phonemes = word.strip(), phonemes.strip().strip("/")
    override(word, phonemes)  # validates
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else [
        f"# Your {ACCENTS[accent]} pronunciation fixes. Format: word<TAB>/phonemes/"]
    lines = [ln for ln in lines if not (ln.split("\t", 1)[0].strip().lower() == word.lower() and "\t" in ln)]
    if note:
        lines.append(f"# {note}")
    lines.append(f"{word}\t/{phonemes}/")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
