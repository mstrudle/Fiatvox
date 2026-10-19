"""Read an EPUB into the book model.

Ported from epub_clean.py (structure, markup cleanup, table-of-contents labels) and narrator_prep.py (which
heading at the top of a section is the section's title). Markup-only cleanup (note references, page-break
markers, superscript note numbers) happens here, while the HTML is still available; text cleanup happens later
in prepare/clean.py on plain paragraphs.
"""
from __future__ import annotations

import html
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from ..model import Book, Section


def zpath(base_dir: str, href: str) -> str:
    """Resolve an href relative to base_dir into a zip member path."""
    href = unquote(href.split("#", 1)[0])
    return posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else posixpath.normpath(href)


def attr(tag: str, name: str) -> str | None:
    m = re.search(r"\b" + name + r'\s*=\s*"([^"]*)"', tag) or re.search(r"\b" + name + r"\s*=\s*'([^']*)'", tag)
    return m.group(1) if m else None


class Epub:
    def __init__(self, path: str | Path):
        self.path = str(path)
        try:
            self.zf = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as e:
            raise ValueError(f"Not a valid EPUB (not a zip file): {self.path}") from e
        self.names = set(self.zf.namelist())
        m = re.search(r'full-path\s*=\s*"([^"]+)"', self.read("META-INF/container.xml"))
        if not m:
            raise ValueError("Could not find the OPF file; is this a valid EPUB?")
        self.opf_path = m.group(1)
        self.opf_dir = posixpath.dirname(self.opf_path)
        self.opf = self.read(self.opf_path)
        self.manifest: dict[str, dict] = {}
        for tag in re.findall(r"<(?:\w+:)?item\b[^>]*>", self.opf):
            iid, href = attr(tag, "id"), attr(tag, "href")
            if not iid or href is None:
                continue
            self.manifest[iid] = {"href": href, "path": zpath(self.opf_dir, href),
                                  "media": attr(tag, "media-type") or "", "props": attr(tag, "properties") or ""}
        spine = [attr(t, "idref") for t in re.findall(r"<(?:\w+:)?itemref\b[^>]*>", self.opf)]
        self.spine = [s for s in spine if s in self.manifest]
        self.nav_path = next((v["path"] for v in self.manifest.values() if "nav" in v["props"].split()), None)
        self.ncx_path = next((v["path"] for v in self.manifest.values()
                              if v["media"] == "application/x-dtbncx+xml"), None)

    def read(self, name: str) -> str:
        return self.read_bytes(name).decode("utf-8", errors="replace")

    def read_bytes(self, name: str) -> bytes:
        if name not in self.names:
            alt = next((n for n in self.names if n.lower() == name.lower()), None)
            if alt is None:
                raise KeyError(name)
            name = alt
        return self.zf.read(name)

    def meta(self, tag: str) -> str:
        m = re.search(r"<dc:" + tag + r"\b[^>]*>(.*?)</dc:" + tag + ">", self.opf, re.S | re.I)
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip() if m else ""

    def cover_path(self) -> str | None:
        for v in self.manifest.values():
            if "cover-image" in v["props"].split():
                return v["path"]
        m = re.search(r'<meta\b[^>]*name\s*=\s*"cover"[^>]*>', self.opf, re.I)
        if m:
            cid = attr(m.group(0), "content")
            if cid in self.manifest and self.manifest[cid]["media"].startswith("image/"):
                return self.manifest[cid]["path"]
        for v in self.manifest.values():
            if v["media"].startswith("image/") and "cover" in v["href"].lower():
                return v["path"]
        return None

    def nav_labels(self) -> dict[str, str]:
        """Content file path -> table-of-contents label (first label wins)."""
        labels: dict[str, str] = {}
        if self.nav_path:
            try:
                nav = self.read(self.nav_path)
                for href, lab in re.findall(r'<a\b[^>]*href\s*=\s*"([^"]*)"[^>]*>(.*?)</a>', nav, re.S | re.I):
                    labels.setdefault(zpath(posixpath.dirname(self.nav_path), href), _flat(plain_text(lab)))
            except KeyError:
                pass
        if not labels and self.ncx_path:
            try:
                ncx = self.read(self.ncx_path)
                for lab, src in re.findall(r"<text>([^<]*)</text>\s*</navLabel>\s*<content\b[^>]*src\s*=\s*\"([^\"]*)\"",
                                           ncx, re.S):
                    labels.setdefault(zpath(posixpath.dirname(self.ncx_path), src), html.unescape(lab).strip())
            except KeyError:
                pass
        return labels


# ----------------------------------------------------------------------------
# markup cleanup (from epub_clean.MARKUP_RULES)
# ----------------------------------------------------------------------------

MARKUP_RULES = [
    # EPUB3 semantic note references
    (re.compile(r"<a\b[^>]*epub:type\s*=\s*\"[^\"]*noteref[^\"]*\"[^>]*>.*?</a>", re.S | re.I), ""),
    (re.compile(r"<a\b[^>]*role\s*=\s*\"doc-noteref\"[^>]*>.*?</a>", re.S | re.I), ""),
    # Superscript markers: numbers, bracketed numbers, ranges, symbols, single letters
    (re.compile(r"<sup\b[^>]*>\s*(?:<a\b[^>]*>)?\s*\[?\s*(?:[\d,\s\-–]{1,15}|[*†‡§a-z])\s*\]?\s*"
                r"(?:</a>)?\s*</sup>", re.S | re.I), ""),
    # Internal links whose entire visible text is a note number: <a href="#n12">12</a> or [12]
    (re.compile(r"<a\b[^>]*href\s*=\s*\"[^\"]*#[^\"]*\"[^>]*>\s*(?:<(?:sup|span|small)\b[^>]*>\s*)*\[?\s*\d{1,3}\s*\]?"
                r"\s*(?:</(?:sup|span|small)>\s*)*</a>", re.S | re.I), ""),
    # Page-break markers that carry visible page numbers
    (re.compile(r"<span\b[^>]*(?:epub:type\s*=\s*\"pagebreak\"|role\s*=\s*\"doc-pagebreak\")[^>]*>.*?</span>",
                re.S | re.I), ""),
    (re.compile(r"<span\b[^>]*class\s*=\s*\"[^\"]*\bpage(?:num|number|no|break)?\b[^\"]*\"[^>]*>\s*\[?\s*(?:p\.?\s*)?"
                r"[\divxlc]{1,6}\s*\]?\s*</span>", re.S | re.I), ""),
    # Paragraphs that contain nothing but a number (stray page numbers)
    (re.compile(r"<p\b[^>]*>\s*\[?\s*\d{1,4}\s*\]?\s*</p>", re.S | re.I), ""),
]

BLOCK_END = re.compile(r"<(br|/p|/div|/h\d|/li|/blockquote|/tr|/dt|/dd|/figcaption|/section|/header)\b[^>]*>", re.I)
HEADING = re.compile(r"<(h[1-3])\b[^>]*>(.*?)</\1>", re.S | re.I)


def body_of(doc: str):
    return re.search(r"(<body\b[^>]*>)(.*)(</body>)", doc, re.S | re.I)


def plain_text(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = BLOCK_END.sub("\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html.unescape(fragment).replace("­", "")
    fragment = re.sub(r"[ \t ]+", " ", fragment)
    fragment = re.sub(r"\n\s*\n+", "\n\n", fragment)
    return fragment.strip()


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def paragraphs_of(fragment: str) -> list[str]:
    """Each block element becomes one paragraph; whitespace inside it is collapsed.

    Line breaks in the source HTML are only formatting, so they are collapsed before block ends become breaks."""
    text = plain_text(re.sub(r"\s+", " ", fragment))
    return [p for p in (_flat(x) for x in text.split("\n")) if p]


def clean_markup(body: str) -> str:
    for pat, repl in MARKUP_RULES:
        body = pat.sub(repl, body)
    return body


def split_heading(body: str, label: str) -> tuple[str, str, str]:
    """(markup before the heading, heading text, markup after) when the section opens with its title heading.

    From narrator_prep: only a heading near the top is the section's title; an h2 or h3 counts only when it
    matches the table-of-contents label."""
    m = HEADING.search(body)
    if not m:
        return "", "", body
    text, level = _flat(plain_text(m.group(2))), m.group(1).lower()
    lab = _norm(label)
    same = lab and _norm(text) and (_norm(text) in lab or lab in _norm(text))
    if not text or len(plain_text(body[:m.start()])) > 300 or (level != "h1" and not same):
        return "", "", body
    return body[:m.start()], text, body[m.end():]


def section_title_guess(doc: str) -> str:
    for pat in (r"<h1\b[^>]*>(.*?)</h1>", r"<h2\b[^>]*>(.*?)</h2>", r"<h3\b[^>]*>(.*?)</h3>",
                r"<title\b[^>]*>(.*?)</title>"):
        m = re.search(pat, doc, re.S | re.I)
        if m:
            t = _flat(plain_text(m.group(1)))
            if t:
                return t
    b = body_of(doc)
    return _flat(plain_text(b.group(2) if b else doc))[:60]


def read_epub(path: str | Path, work: Path | None = None) -> Book:
    """The whole book, every spine section, nothing dropped yet. Cover image copied into `work` if given."""
    ep = Epub(path)
    labels = ep.nav_labels()
    book = Book(title=ep.meta("title"), author=ep.meta("creator"), source=str(path), kind="epub")
    for iid in ep.spine:
        item = ep.manifest[iid]
        try:
            doc = ep.read(item["path"])
        except KeyError:
            continue
        b = body_of(doc)
        body = clean_markup(b.group(2) if b else doc)
        label = labels.get(item["path"], "")
        before, heading, after = split_heading(body, label)
        lead = paragraphs_of(before)
        sec = Section(title="", paragraphs=lead + paragraphs_of(after), source=posixpath.basename(item["href"]),
                      label=label, heading=heading or "", lead=len(lead) if heading else 0,
                      is_nav="nav" in item["props"].split())
        if not heading:
            sec.heading = ""
            sec.label = label or section_title_guess(doc)
        sec.title = heading or label or section_title_guess(doc)
        book.sections.append(sec)
    cover = ep.cover_path()
    if cover and work is not None:
        try:
            data = ep.read_bytes(cover)
            ext = posixpath.splitext(cover)[1].lower() or ".jpg"
            out = Path(work) / f"cover{ext}"
            out.write_bytes(data)
            book.cover = str(out)
        except KeyError:
            pass
    return book
