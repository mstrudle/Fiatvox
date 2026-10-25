"""Read RTF, DOCX, DOC, ODT, HTML and plain text through macOS's built-in `textutil` (no Calibre, no Homebrew).

textutil turns the file into HTML whose CSS keeps each paragraph's size and weight. Paragraphs set larger or bold,
short, and not ending like a sentence, or lines such as "BOOK I" and "CHAPTER III.", start a new section; runs of
such lines ("BOOK I" + "ON THE NATURE OF WAR") form one heading. From there the book goes through the same
decisions as an EPUB: the AI drops sections and retitles them.
"""
from __future__ import annotations

import collections
import html
import re
import subprocess
from pathlib import Path

from ..model import Book, Section

SUFFIXES = (".rtf", ".rtfd", ".docx", ".doc", ".odt", ".txt", ".html", ".htm", ".webarchive", ".wordml")
CHAPTERISH = re.compile(r"^(?:BOOK|PART|CHAPTER|SECTION|VOLUME|LECTURE|APPENDIX|PREFACE|FOREWORD|INTRODUCTION|"
                        r"PROLOGUE|EPILOGUE|CONCLUSION|AFTERWORD)\b(?:\s+[IVXLC\d]+\b)?[.:]?", re.I)


def to_html(src: Path, out: Path) -> Path:
    r = subprocess.run(["textutil", "-convert", "html", "-output", str(out), str(src)], capture_output=True,
                       text=True, timeout=300)
    if r.returncode != 0 or not out.exists():
        raise ValueError(f"textutil could not read {src.name}: {(r.stderr or r.stdout).strip()[:300]}")
    return out


def _styles(doc: str) -> dict[str, tuple[float, bool]]:
    """CSS class -> (font size in px, bold)."""
    out = {}
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", doc, re.S | re.I))
    for name, body in re.findall(r"(?:p|span)\.(\w+)\s*\{([^}]*)\}", css):
        size = re.search(r"([\d.]+)px", body)
        bold = bool(re.search(r"font:\s*bold|font-weight:\s*(bold|[6-9]00)", body))
        out[name] = (float(size.group(1)) if size else 0.0, bold)
    return out


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def paragraphs(doc: str) -> list[tuple[str, float, bool]]:
    """(text, font size, bold) for every paragraph."""
    styles = _styles(doc)
    out = []
    for attrs, inner in re.findall(r"<p\b([^>]*)>(.*?)</p>", doc, re.S | re.I):
        text = _flat(inner)
        if not text:
            continue
        cls = re.search(r'class="([^"]+)"', attrs)
        size, bold = styles.get(cls.group(1), (0.0, False)) if cls else (0.0, False)
        spans = re.findall(r'<span\b[^>]*class="([^"]+)"', inner)
        if spans and all(styles.get(c, (0.0, False))[1] for c in spans):
            bold = True  # the whole paragraph is set in bold spans
        out.append((text, size, bold))
    return out


def is_heading(text: str, size: float, bold: bool, body_size: float) -> bool:
    words = len(text.split())
    if words > 14 or not text:
        return False
    if CHAPTERISH.match(text):
        return True
    sentence_end = text.rstrip()[-1:] in ".!?,;" and not re.search(r"\b[IVXLC]+\.$|\d\.$", text)
    letters = [c for c in text if c.isalpha()]
    caps = letters and sum(c.isupper() for c in letters) >= 0.8 * len(letters) and words <= 10
    bigger = body_size and size >= body_size * 1.15
    return (bigger or bold or caps) and not sentence_end


def read_document(src: str | Path, work: Path) -> Book:
    src = Path(src)
    doc = to_html(src, Path(work) / "converted.html").read_text(encoding="utf-8", errors="replace")
    paras = paragraphs(doc)
    if not paras:
        raise ValueError(f"No text found in {src.name}")
    sizes = collections.Counter(round(s, 1) for _, s, _ in paras for _ in range(1))
    body_size = sizes.most_common(1)[0][0] if sizes else 0.0
    title = _flat(m.group(1)) if (m := re.search(r"<title>(.*?)</title>", doc, re.S | re.I)) else ""
    author = ""
    if m := re.search(r'<meta name="[Aa]uthor" content="([^"]*)"', doc):
        author = html.unescape(m.group(1)).strip()
    book = Book(title=title or src.stem, author=author, source=str(src), kind="doc")
    cur, heads = None, []
    for text, size, bold in paras:
        if is_heading(text, size, bold, body_size):
            heads.append(text)
            continue
        if heads:
            label = ". ".join(h.rstrip(".") for h in heads)
            cur = Section(title=label, label=label, heading=label, source=f"para {len(book.sections) + 1}")
            book.sections.append(cur)
            heads = []
        if cur is None:
            cur = Section(title="Title Page", label="", source="start")
            book.sections.append(cur)
        cur.paragraphs.append(text)
    if heads:  # headings at the very end with nothing after them
        label = ". ".join(h.rstrip(".") for h in heads)
        book.sections.append(Section(title=label, label=label, heading=label, source="end"))
    return book
