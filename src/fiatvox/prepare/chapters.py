"""Chapter names and the flat chapter list. Ported from narrator_prep.prepare, on the book model.

  1. Uniform names: "Chapter Two. The History of Eggs" -> "Chapter Two: The History of Eggs".
  2. One sequence through the book: "Chapter" numbers are renumbered in reading order, so a book that restarts
     its numbering in each part still gets Chapter One, Two, ... Twenty-Eight.
  3. The book's own "Chapter 3" label printed above a bare title is kept in the name.
  4. Stray labels and duplicated subtitles next to the heading are removed, so the title is not spoken twice.
  5. One section is one chapter marker: the list is flat by construction.
Chapter names are never normalised ("Napoleon III" stays as written in Books); only the spoken copy is.
"""
from __future__ import annotations

import re

from ..model import Book
from ..narrate.normalise import NUMBER_WORDS, cardinal, roman

LABEL_WORDS = r"(?:Chapter|Part|Section|Book|Volume)"
TITLE_PREFIX = re.compile(
    r"^(\s*)((?:" + LABEL_WORDS + r")\s+[A-Z0-9][A-Za-z0-9-]*|Introduction|Conclusion|Appendix(?:\s+[A-Z][A-Za-z-]*)?"
    r"|Epilogue|Prologue|Preface|Foreword|Afterword)\.\s+(?=\S)")
BARE_LABEL = re.compile(r"^(?:(?:chapter|part|section|book|volume)\s+[\w-]+|\d+|[ivxlc]+)\.?$", re.I)
LABEL_LINE = re.compile(r"^(chapter|part|section|book|volume)\s+([\w-]+)\.?$", re.I)
CHAPTER_NUM = re.compile(r"^Chapter\s+([A-Za-z]+(?:-[A-Za-z]+)?|\d+)\b(?=\s*(?::|$))")
WORD_NUMBERS = {cardinal(n): n for n in range(1, 100)}


def norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def uniform_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    t = TITLE_PREFIX.sub(r"\1\2: ", t)
    return "Title Page" if t.lower() == "front matter" else t


SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of", "on", "or",
               "the", "to", "with", "vs", "via"}
NUMBER_LABEL = re.compile(r"^\[?\s*((?:part|book|volume|section)\s+)?([A-Za-z]+(?:-[A-Za-z]+)?|\d+)\s*\]?\s*[-–—:.]\s*(\S.*)$",
                          re.I)
ACRONYM = re.compile(r"^(?:[A-Z]\.){2,}$|^(?:UK|USSR|NATO|EU|RMA|WWI|WWII|CIA|FBI|NASA|DARPA|IED|UAV|UAVS)$")


def smart_case(t: str) -> str:
    """Title case for a title printed in capitals: "ROBOTICS FOR DUMMIES" -> "Robotics for Dummies"."""
    letters = [c for c in t if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) < 0.8 * len(letters):
        return t
    words = t.split(" ")
    out = []
    for i, w in enumerate(words):
        core = re.sub(r"[^\w.'’-]", "", w)
        prev_low = out[-1].lower() if out else ""
        if (ACRONYM.match(core) or re.fullmatch(r"[IVXLC]+", core) and len(core) > 1
                or core in ("US", "UN", "AI") and prev_low == "the"):
            out.append(w)
            continue
        low = w.lower()
        prev = out[-1] if out else ""
        if i and low.strip("“\"'(") in SMALL_WORDS and not prev.endswith((":", ".", "?", "!")):
            out.append(low)
        else:
            out.append("-".join(x[:1].upper() + x[1:] if x else x for x in re.split(r"(?<=[\w’'])-", low))
                       if not low[:1].isalpha() else low[:1].upper() + low[1:])
    return " ".join(out)


def tidy_title(t: str) -> str:
    """Make a raw table-of-contents label speakable: "[ONE] - INTRODUCTION: SCENES" -> "Chapter One: Introduction:
    Scenes"; "[PART TWO] - WHAT CHANGE" -> "Part Two: What Change". Titles already in good form are unchanged."""
    t = re.sub(r"\s+", " ", t).strip()
    m = NUMBER_LABEL.match(t)
    if m and number_value(m.group(2)):
        kind = (m.group(1) or "Chapter").strip().title()
        word = cardinal(number_value(m.group(2))).title() if number_value(m.group(2)) < 100 else m.group(2)
        return f"{kind} {word}: {smart_case(m.group(3).strip())}"
    m = re.match(r"^\[\s*([^\]]+?)\s*\]\s*$", t)  # "[ACKNOWLEDGMENTS]"
    if m:
        t = m.group(1)
    return smart_case(t)


def number_value(token: str) -> int:
    """'3', 'III', 'three', 'Twenty-One' -> int, or 0."""
    if token.isdigit():
        return int(token)
    v = roman(token.upper())
    if v:
        return v
    return WORD_NUMBERS.get(token.lower(), 0)


def _is_echo(text: str, title: str, echoes: set[str]) -> bool:
    """A short paragraph that only repeats the title, part of it, or a bare label like '[ONE]' or 'Chapter 7'."""
    t = text.strip()
    if not t or len(t) >= 100:
        return False
    n = norm(t)
    if not n:
        return True  # only punctuation or ornaments
    if BARE_LABEL.match(n) or n in echoes or n in NUMBER_WORDS or n.replace(" ", "-") in WORD_NUMBERS:
        return True
    if f" {n} " in f" {norm(title)} ":
        return True
    # a subtitle repeated with an extra label word: "INTRODUCTION: SCENES FROM A ROBOT WAR"
    words, tw = n.split(), set(norm(title).split())
    return len(words) >= 3 and sum(w in tw for w in words) >= 0.75 * len(words)


def name_section(sec, n: int) -> list[str]:
    """Settle one section's title and remove stray labels next to it. Returns the removed texts."""
    removed = []
    title = uniform_title(tidy_title(sec.title or sec.heading or sec.label or f"Section {n}"))
    lead = sec.paragraphs[:sec.lead]
    if sec.heading and not re.match(LABEL_WORDS + r"\b", title, re.I):
        # the book prints its own "Chapter 3" label above a bare title: keep the number in the name
        for p in lead:
            lm = LABEL_LINE.match(p.strip())
            if lm:
                v = number_value(lm.group(2))
                word = cardinal(v).title() if 0 < v < 100 else lm.group(2).title()
                title = f"{lm.group(1).title()} {word}: {title}"
                break
    sec.title = title
    echoes = {norm(title), norm(title.split(": ", 1)[-1]), norm(title.split(": ", 1)[0])} - {""}
    # paragraphs printed before the heading, then the first few after it
    kept_lead = []
    for p in lead:
        if _is_echo(p, title, echoes):
            removed.append(p)
        else:
            kept_lead.append(p)
    rest = sec.paragraphs[sec.lead:]
    while rest and len(removed) < 6 and _is_echo(rest[0], title, echoes):
        removed.append(rest.pop(0))
    sec.paragraphs = kept_lead + rest
    sec.lead = 0
    return removed


def renumber_chapters(book: Book) -> list[tuple[str, str]]:
    """Chapter numbers in one sequence through the book. Returns (old, new) for every change."""
    changes = []
    k = 0
    for sec in book.sections:
        m = CHAPTER_NUM.match(sec.title)
        if not m:
            continue
        k += 1
        word = cardinal(k).title() if k < 100 else str(k)
        if m.group(1) != word:
            new = f"Chapter {word}" + sec.title[m.end():]
            changes.append((sec.title, new))
            sec.title = new
    return changes


def prepare_chapters(book: Book) -> dict:
    """Name every section and drop sections with nothing to say. Returns what was done, for the QC report."""
    removed, empty = [], []
    for n, sec in enumerate(book.sections, 1):
        for t in name_section(sec, n):
            removed.append((sec.source, t))
    kept = []
    for sec in book.sections:
        if not sec.paragraphs and not sec.title:
            empty.append(sec.source)
        else:
            kept.append(sec)
    book.sections = kept
    renumbered = renumber_chapters(book)
    return {"sections": len(book.sections), "titles": [s.title for s in book.sections],
            "removed_labels": removed, "renumbered": renumbered, "empty_dropped": empty}
