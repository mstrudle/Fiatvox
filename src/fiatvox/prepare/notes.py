"""Endnote lists at the end of a chapter: a "Notes" heading and numbered citations, or numbered citations alone.
The AI can only drop whole sections, so notes printed inside a chapter's own file or pages are cut here."""
from __future__ import annotations

import re

from ..model import Book

HEAD_RE = re.compile(r"^(?:(?:chapter\s+)?(?:end)?notes(?:\s+(?:and|&)\s+(?:references|sources))?"
                     r"(?:\s+to\s+chapter\s+\S+)?|references|sources|works\s+cited)[.:]?$", re.I)
GLUED_RE = re.compile(r"^(?:end)?notes:?\s+1[.)]?\s+\S", re.I)   # "NOTES 1. Historians..." from a PDF line
NUM_RE = re.compile(r"^\s*\[?(\d{1,3})[.)\]]?\s+\S")
CITE_RE = re.compile(r"\b(?:1[5-9]\d\d|20\d\d)\b|\bibid\b|\bop\.\s*cit\b|\bpp?\.\s*\d|\bPress\b|\bPublish|"
                     r"\btrans\.|\beds?\.|\bvol\.|\bcf\.|\d+\s*[–-]\s*\d+\.\s*$", re.I)


def _cites(paras: list[str]) -> float:
    return sum(1 for p in paras if CITE_RE.search(p)) / max(1, len(paras))


def _numbered(paras: list[str]) -> float:
    return sum(1 for p in paras if NUM_RE.match(p)) / max(1, len(paras))


def notes_start(paras: list[str]) -> int | None:
    """Where a trailing notes list begins in a section's paragraphs, or None."""
    # a "Notes" heading followed, to the end of the section, by numbered entries or citations (or by nothing,
    # when the notes pages themselves were cut); PDF notes come as wrapped lines, so fewer show a citation
    for i in range(len(paras) - 1, -1, -1):
        p = paras[i].strip()
        glued = bool(GLUED_RE.match(p))
        if glued or (len(p) < 40 and HEAD_RE.match(p)):
            rest = paras[i:] if glued else paras[i + 1:]
            if not rest or _numbered(rest) >= 0.6 or _cites(rest) >= 0.4:
                return i
            break
    # no heading: a run of entries numbered from 1 to the end of the section, mostly citations
    nums = [(i, int(m.group(1))) for i, p in enumerate(paras) if (m := NUM_RE.match(p))]
    for k, (i, n) in enumerate(nums):
        if n != 1:
            continue
        run = [x for _, x in nums[k:]]
        block = paras[i:]
        if (len(run) >= 3 and run == list(range(1, len(run) + 1)) and _numbered(block) >= 0.7
                and _cites([paras[j] for j, _ in nums[k:]]) >= 0.5):
            return i
    return None


def strip_notes(book: Book) -> list[str]:
    """Cut trailing notes lists from every section, in place; one line per section cut, for the QC report."""
    out = []
    for s in book.sections:
        i = notes_start(s.paragraphs)
        if i is None:
            continue
        n = len(s.paragraphs) - i
        s.paragraphs = s.paragraphs[:i]
        out.append(f"{s.title or 'untitled section'}: {n} paragraph(s) of endnotes")
    return out
