"""OCR and conversion damage audit. Ported from epub_clean.compute_audit and paragraph_issues.

Runs on clean text before any pronunciation override is inserted, so override markup is never counted as damage.
"""
from __future__ import annotations

import collections
import functools
import re

from ..model import Book
from .clean import LIGATURE_RE

DICT_PATHS = ["/usr/share/dict/words", "/usr/share/dict/web2", "/usr/share/dict/american-english"]


@functools.lru_cache(maxsize=1)
def dictionary() -> frozenset[str]:
    words = set()
    for p in DICT_PATHS:
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                words.update(w.strip().lower() for w in f if w.strip())
        except OSError:
            pass
    return frozenset(words)


SUFFIXES = ("'s", "’s", "s", "es", "ed", "d", "ing", "ly", "er", "ers", "est", "ies", "ied", "ness", "ment",
            "ments", "ation", "ations", "ism", "ist", "ists", "ity", "al", "ally", "able", "ful", "less", "ise", "ize",
            "ised", "ized")
BRIT = [("our", "or"), ("isation", "ization"), ("ise", "ize"), ("ised", "ized"), ("ising", "izing"), ("yse", "yze"),
        ("tre", "ter"), ("lled", "led"), ("lling", "ling"), ("ae", "e"), ("oe", "e"), ("ogue", "og"), ("ence", "ense")]


def known_word(w: str, words, _depth: int = 0) -> bool:
    if w in words:
        return True
    if _depth == 0:
        for a, b in BRIT:
            if a in w and known_word(w.replace(a, b), words, 1):
                return True
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[: -len(suf)]
            if _depth == 0 and known_word(stem, words, 1):
                return True
            if stem in words or stem + "e" in words or (suf in ("ies", "ied") and stem + "y" in words):
                return True
            if len(stem) > 3 and stem[-1] == stem[-2] and stem[:-1] in words:  # running -> run
                return True
    if "-" in w:
        return all(known_word(p, words, _depth) for p in w.split("-") if p)
    return False


TOKEN_RE = re.compile(r"[A-Za-zÀ-ſ0-9'’\-•|~^{}\\<>■□]+")
PATTERN_CHECKS = [
    ("letters and digits mixed", re.compile(r"[a-z]\d+[a-z]|\d[a-z]{4,}|^[a-z]{2,}\d+$")),
    ("stray symbol in word", re.compile(r"[a-zA-Z][•|~^{}\\<>■□]|[•|~^{}\\<>■□][a-zA-Z]")),
    ("odd capitalisation", re.compile(r"^[a-z]+[A-Z]{2,}|^[a-z]{2,}[A-Z][a-z]|"
                                      r"^(?!Mc|Mac|De|Di|Da|La|Le|Van|Von|Du|Fitz|O')[A-Z][a-z]{1,5}[A-Z]")),
]
STRAY_LETTER = re.compile(r"(?<![\w(\[.'’&-])\b[b-hj-z]\b(?![\w.)\]'’&-])")


def paragraph_issues(text: str, freq, words) -> list[tuple[str, str]]:
    """(token, reason) pairs that look like OCR damage."""
    issues = []
    for m in LIGATURE_RE.finditer(text):
        issues.append((" ".join(text[max(0, m.start() - 6): m.end() + 6].split()), "split ligature"))
    for m in re.finditer(r"[a-z]- [a-z]", text):
        issues.append((" ".join(text[max(0, m.start() - 8): m.end() + 8].split()), "split hyphenation"))
    for m in STRAY_LETTER.finditer(text):
        issues.append((" ".join(text[max(0, m.start() - 10): m.end() + 10].split()), "stray single letter"))
    for tok in TOKEN_RE.findall(text):
        t = tok.strip("'’-")
        if not t:
            continue
        for reason, pat in PATTERN_CHECKS:
            if pat.search(t):
                issues.append((t, reason))
                break
        else:
            if words and t.isalpha() and t.islower() and len(t) >= 3:
                if freq[t] <= 1 and not known_word(t, words):
                    issues.append((t, "unknown word"))
    return issues


def book_freq(book: Book) -> collections.Counter:
    freq = collections.Counter()
    for s in book.sections:
        for tok in TOKEN_RE.findall(s.text):
            freq[tok.strip("'’-").lower()] += 1
    return freq


def verdict(rate: float) -> str:
    if rate < 1.5:
        return "CLEAN"
    if rate < 6:
        return "MINOR"
    if rate < 25:
        return "NOTICEABLE"
    return "HEAVY"


def audit(book: Book) -> dict:
    words = dictionary()
    freq = book_freq(book)
    total_words = total_issues = 0
    examples = collections.defaultdict(list)
    counts = collections.Counter()
    sections = []
    for n, s in enumerate(book.sections, 1):
        wc = s.words
        iss = []
        for p in s.paragraphs:
            iss += paragraph_issues(p, freq, words)
        total_words += wc
        total_issues += len(iss)
        for tok, reason in iss:
            counts[reason] += 1
            if len(examples[reason]) < 12 and tok not in examples[reason]:
                examples[reason].append(tok)
        sections.append({"n": n, "words": wc, "issues": len(iss),
                         "rate": round(1000 * len(iss) / wc, 1) if wc else 0.0, "title": s.title})
    rate = 1000 * total_issues / total_words if total_words else 0
    return {"words": total_words, "issues": total_issues, "rate": round(rate, 2), "verdict": verdict(rate),
            "sections": sections, "examples": dict(examples), "counts": dict(counts), "dictionary": bool(words)}


def flagged_paragraphs(book: Book) -> list[tuple[int, int, str]]:
    """(section index, paragraph index, text) for every paragraph with at least one issue."""
    words, freq = dictionary(), book_freq(book)
    out = []
    for si, s in enumerate(book.sections):
        for pi, p in enumerate(s.paragraphs):
            if paragraph_issues(p, freq, words):
                out.append((si, pi, p))
    return out


def flag_contexts(book: Book, limit: int = 60) -> list[dict]:
    """Audit flags with surrounding text, so the AI can write exact replacements."""
    words, freq = dictionary(), book_freq(book)
    out = []
    for n, s in enumerate(book.sections, 1):
        flat = " ".join(s.text.split())
        for p in s.paragraphs:
            for tok, reason in paragraph_issues(p, freq, words):
                i = flat.find(tok)
                ctxt = flat[max(0, i - 70): i + len(tok) + 50] if i >= 0 else tok
                out.append({"n": n, "reason": reason, "token": tok, "context": ctxt})
                if len(out) >= limit:
                    return out
    return out
