"""Text cleanup on plain paragraphs. Ported from epub_clean.clean_text_segment and build_pron_rules.

Flags (turned on by the AI or the heuristics, never by default):
  fix_ligatures        rejoin words split after fi/fl ("fi ction", "offi cer")
  strip_glued_numbers  delete note numbers glued to words or punctuation ("war.23 The")
  strip_citations      delete parenthetical author-year citations "(Smith 2019, 45)"
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..model import Book

FLAGS = ("fix_ligatures", "strip_glued_numbers", "strip_citations")

# Built-in fixes applied before book-specific replacements.
DEFAULT_REPLACEMENTS = [
    ("e.g.", "for example"), ("E.g.", "For example"), ("i.e.", "that is"), ("I.e.", "That is"),
    ("cf.", "compare"), ("Cf.", "Compare"), ("et al.", "and colleagues"), ("viz.", "namely"),
    ("etc.", "et cetera"), ("vs.", "versus"), ("ibid.", ""), ("Ibid.", ""),
]

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<]+")
HYPHEN_RE = re.compile(r"([a-z])-[ \t]+(?!(?:and|or|to|nor|as|through)\b)([a-z])")
GLUED_NUM_RE = re.compile(r"(?<=[A-Za-z\.,;:”’\"\)])\d{1,3}(?=[\s ]|$)")
SUPERSCRIPT_RE = re.compile(r"[⁰¹²³⁴-⁹]+")
SQUARED_RE = re.compile(r"\b(km|m|mi|ft|cm|in)²")
CUBED_RE = re.compile(r"\b(km|m|mi|ft|cm|in)³")
LIGATURE_RE = re.compile(r"(?<!-)((?:ff|f|F)[il]) (?=[a-z])")
CITATION_RE = re.compile(
    r"\s?\((?:see |cf\. )?[A-Z][A-Za-zÀ-ſ'\-]+(?: (?:and|&amp;|&) [A-Z][A-Za-z'\-]+)?(?: et al\.)?,? \d{4}[a-z]?"
    r"(?:[,:] (?:pp?\. ?)?[\d\-–, ]+)?(?:; [^)]{1,80})?\)")


@dataclass
class CleanOptions:
    fix_ligatures: bool = False
    strip_glued_numbers: bool = False
    strip_citations: bool = False
    keep_urls: bool = False

    @classmethod
    def from_flags(cls, flags) -> "CleanOptions":
        return cls(**{f: True for f in (flags or []) if f in FLAGS})


def replacement_rules(pairs) -> list[tuple[re.Pattern, str]]:
    """Exact, case-sensitive, whole-word find/replace rules (the old --pronounce format)."""
    rules = []
    for find, repl in pairs:
        find = str(find)
        if not find.strip():
            continue
        pat = re.compile(r"(?<![\w])" + re.escape(find) + (r"(?![\w])" if find[-1].isalnum() else ""))
        rules.append((pat, str(repl)))
    return rules


def clean_text(t: str, opts: CleanOptions, rules) -> str:
    t = t.replace("­", "")
    if not opts.keep_urls:
        t = URL_RE.sub("", t)
    t = HYPHEN_RE.sub(r"\1\2", t)
    t = SQUARED_RE.sub(r"\1 squared", t)
    t = CUBED_RE.sub(r"\1 cubed", t)
    t = SUPERSCRIPT_RE.sub("", t)
    if opts.fix_ligatures:
        t = LIGATURE_RE.sub(r"\1", t)
    t = re.sub(r"\(\*+\)|\[\*+\]", "", t)
    if opts.strip_glued_numbers:
        t = GLUED_NUM_RE.sub("", t)
    if opts.strip_citations:
        t = CITATION_RE.sub("", t)
    for pat, repl in rules:
        t = pat.sub(repl, t)
    # tidy spacing left behind by deletions
    t = re.sub(r"(?<=\w)[ \t]+([,.;:!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def clean_book(book: Book, flags=(), replacements=(), keep_urls: bool = False) -> Book:
    """A cleaned copy: built-in fixes, flags, then the book's own replacements; applied to titles too."""
    opts = CleanOptions.from_flags(flags)
    opts.keep_urls = keep_urls
    rules = replacement_rules(list(DEFAULT_REPLACEMENTS) + [tuple(p) for p in replacements])
    out = book.copy()
    for s in out.sections:
        s.paragraphs = [p for p in (clean_text(p, opts, rules) for p in s.paragraphs) if p]
        s.title = clean_text(s.title, CleanOptions(keep_urls=True), rules) or s.title
    return out
