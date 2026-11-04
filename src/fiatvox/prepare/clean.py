"""Text cleanup on plain paragraphs. Ported from epub_clean.clean_text_segment and build_pron_rules.

Flags (turned on by the AI or the heuristics, never by default):
  fix_ligatures        rejoin words split after fi/fl ("fi ction", "offi cer")
  strip_glued_numbers  delete note numbers glued to words or punctuation ("war.23 The")
  strip_citations      delete parenthetical author-year citations "(Smith 2019, 45)"
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..model import Book

FLAGS = ("fix_ligatures", "strip_glued_numbers", "strip_citations")

# Built-in fixes applied before book-specific replacements.
DEFAULT_REPLACEMENTS = [
    ("e.g.", "for example"), ("E.g.", "For example"), ("i.e.", "that is"), ("I.e.", "That is"),
    ("cf.", "compare"), ("Cf.", "Compare"), ("et al.", "and colleagues"), ("viz.", "namely"),
    ("etc.", "et cetera"), ("vs.", "versus"), ("ibid.", ""), ("Ibid.", ""),
]

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<]+")
# a word broken at a line end: "back- grounds", or "back¬ grounds" where a PDF prints the break as a NOT SIGN
HYPHEN_RE = re.compile(r"([A-Za-z]*[a-z])(?:-[ \t]+|¬[ \t]*)(?!(?:and|or|to|nor|as|through)\b)([a-z][a-z’']*)")
END_RE = re.compile(r"[.!?…:;)\]]['\"’”)]*\s*$")
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


@functools.lru_cache(maxsize=1)
def _lexicon() -> frozenset[str]:
    """The words Kokoro's pronunciation dictionaries know, which include forms the Mac word list lacks
    ("began", "airmen"), plus that word list."""
    from .audit import dictionary
    words = set(dictionary())
    try:
        import misaki.data
        base = Path(misaki.data.__file__).parent
        for name in ("us_gold.json", "gb_gold.json"):
            words.update(k.lower() for k in json.loads((base / name).read_text(encoding="utf-8")))
    except (ImportError, OSError, ValueError):
        pass
    return frozenset(words)


def _rejoin(m: re.Match) -> str:
    """One word if that is a word ("backgrounds"); a hyphen if only the halves are ("cold-blooded")."""
    from .audit import known_word
    a, b = m.group(1), m.group(2)
    tail = b[len(b.rstrip("’'")):]
    b = b.rstrip("’'")
    words = _lexicon()
    if not words or known_word((a + b).lower(), words):
        return a + b + tail
    if known_word(a.lower(), words) and known_word(b.lower(), words) and b.lower() not in ("man", "men"):
        return f"{a}-{b}{tail}"
    return a + b + tail


def join_broken(paragraphs: list[str]) -> list[str]:
    """Rejoin paragraphs a PDF split mid-sentence (each printed line of a quotation, or a page break), which
    would otherwise each get a paragraph pause: the first stops without closing punctuation and the next
    starts in lower case, or the first ends in a broken word."""
    out: list[str] = []
    for p in paragraphs:
        if out:
            prev = out[-1].rstrip()
            if re.search(r"[A-Za-z][-¬]$", prev) and re.match(r"\s*[a-z]", p):
                out[-1] = prev + " " + p.lstrip()      # the hyphen rule then decides "cold-blooded" or "everyday"
                continue
            if prev and not END_RE.search(prev) and re.match(r"\s*[a-z]", p):
                out[-1] = prev + " " + p.lstrip()
                continue
        out.append(p)
    return out


def clean_text(t: str, opts: CleanOptions, rules) -> str:
    t = t.replace("­", "")
    if not opts.keep_urls:
        t = URL_RE.sub("", t)
    t = HYPHEN_RE.sub(_rejoin, t)
    t = t.replace("¬", "-")
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


def clean_book(book: Book, flags=(), replacements=(), keep_urls: bool = False, join: bool = False) -> Book:
    """A cleaned copy: built-in fixes, flags, then the book's own replacements; applied to titles too. With
    `join` (PDFs and documents), paragraphs split mid-sentence are put back together first."""
    opts = CleanOptions.from_flags(flags)
    opts.keep_urls = keep_urls
    rules = replacement_rules(list(DEFAULT_REPLACEMENTS) + [tuple(p) for p in replacements])
    out = book.copy()
    for s in out.sections:
        paras = join_broken(s.paragraphs) if join else s.paragraphs
        s.paragraphs = [p for p in (clean_text(p, opts, rules) for p in paras) if p]
        s.title = clean_text(s.title, CleanOptions(keep_urls=True), rules) or s.title
    return out
