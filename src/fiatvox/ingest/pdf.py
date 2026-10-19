"""Read a book PDF into the book model. Ported from pdf2epub.py.

  * Rebuilds lines and paragraphs from word positions (born-digital PDFs and PDFs with an OCR text layer).
  * Removes running headers, footers and page numbers (text repeated at the top or bottom of many pages),
    including misspelled headers and headers glued to the first line of text.
  * Removes footnotes (small type at the bottom of the page) and superscript note numbers.
  * Rejoins words hyphenated across lines and paragraphs split across pages; reads two-column pages by column.
  * Places chapters from an explicit list (the AI's decisions), the PDF's bookmarks, or large headings.
"""
from __future__ import annotations

import collections
import difflib
import re
import statistics
from dataclasses import dataclass, field

import pymupdf

from ..model import Book, Section

ROMAN = re.compile(r"^[ivxlcdm]+$", re.I)
TERMINAL = tuple(".!?:;\"'”’)]—")
PAGE_TOKEN = re.compile(r"^[\[(]?(?:\d{1,4}|[ivxlcIVXLC|]{1,6}|[ivxlcIVXLC]*\d+[ivxlcIVXLC]*)[\])]?$")
CHAPTERISH = re.compile(r"^(C\W?H\W?A\W?P|CHA ?P|PART\b|BOOK\b|APPENDI|PREFACE|FOREWORD|INTRODUCTION|PROLOGUE|EPILOGUE|"
                        r"CONCLUSION|CONTENTS|INDEX|BIBLIOGRAPHY|NOTES|ACKNOWLEDG|TRANSLATOR|EDITOR|AFTERWORD|DEDICATION)",
                        re.I)


@dataclass
class Stats:
    markers: int = 0
    two_col: int = 0
    removed: collections.Counter = field(default_factory=collections.Counter)


class Line:
    __slots__ = ("text", "x0", "x1", "y0", "y1", "h", "page", "words", "col")

    def __init__(self, words, page, col=0):
        self.col = col
        words = sorted(words, key=lambda w: w[0])
        self.words = words
        self.text = " ".join(w[4] for w in words)
        self.x0 = min(w[0] for w in words)
        self.x1 = max(w[2] for w in words)
        self.y0 = statistics.median(w[1] for w in words)
        self.y1 = statistics.median(w[3] for w in words)
        self.h = statistics.median(w[3] - w[1] for w in words)
        self.page = page


# ----------------------------------------------------------------------------
# layout reconstruction
# ----------------------------------------------------------------------------

def superscript_spans(page):
    """Bounding boxes and text of superscript note markers on the page."""
    sups = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            spans = ln["spans"]
            if not spans:
                continue
            sizes = sorted(sp["size"] for sp in spans)
            med = sizes[len(sizes) // 2]
            base = max(sp["origin"][1] for sp in spans)
            for sp in spans:
                t = sp["text"].strip()
                if not t or not re.fullmatch(r"[\d,\*†‡§\-–]+", t):
                    continue
                raised = sp["origin"][1] < base - sp["size"] * 0.2
                if (sp["flags"] & 1) or (sp["size"] < med * 0.8 and raised):
                    sups.append((sp["bbox"], t))
    return sups


def strip_superscripts(words, sups):
    if not sups:
        return words
    out = []
    for w in words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        for (sx0, sy0, sx1, sy1), st in sups:
            if sx0 >= x0 - 1 and sx1 <= x1 + 1 and sy0 < y1 and sy1 > y0:
                i = text.rfind(st)
                if i >= 0:
                    text = text[:i] + text[i + len(st):]
        if text.strip():
            out.append((x0, y0, x1, y1, text) + tuple(w[5:]))
    return out


def find_gutter(words, W):
    """The x position of a two-column gutter, or None for single-column pages."""
    if len(words) < 80:
        return None
    best, best_x = None, None
    for x in range(int(W * 0.35), int(W * 0.65), 2):
        c = sum(1 for w in words if w[0] < x < w[2])
        if best is None or c < best:
            best, best_x = c, x
    left = sum(1 for w in words if w[2] <= best_x)
    right = sum(1 for w in words if w[0] >= best_x)
    if best <= max(2, len(words) * 0.01) and left > len(words) * 0.25 and right > len(words) * 0.25:
        return best_x
    return None


def group_lines(words, pno, med_h):
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines, cur, cur_mid = [], [], None
    for w in words:
        mid = (w[1] + w[3]) / 2
        if cur and abs(mid - cur_mid) > med_h * 0.55:
            lines.append(Line(cur, pno))
            cur = []
        cur.append(w)
        cur_mid = statistics.mean((x[1] + x[3]) / 2 for x in cur)
    if cur:
        lines.append(Line(cur, pno))
    lines.sort(key=lambda l: l.y0)
    return lines


def page_lines(pd: "PageData", pno, stats: Stats):
    """Visual lines from word positions, reading two-column pages column by column."""
    words = [w for w in pd.words if w[4].strip()]
    n0 = sum(len(w[4]) for w in words)
    words = strip_superscripts(words, pd.sups)
    stats.markers += int(n0 != sum(len(w[4]) for w in words))
    if not words:
        return []
    med_h = statistics.median(w[3] - w[1] for w in words)
    g = find_gutter(words, pd.width)
    full = group_lines(words, pno, med_h)
    if g is None:
        return full
    stats.two_col += 1
    top, left, right, bottom = [], [], [], []
    seen_split = False
    for l in full:
        ws = sorted(l.words, key=lambda w: w[0])
        spans_gutter = any(a[2] < g < b[0] and b[0] - a[2] < med_h * 1.2 for a, b in zip(ws, ws[1:])) \
            or any(w[0] < g < w[2] for w in ws)
        lw = [w for w in ws if (w[0] + w[2]) / 2 < g]
        rw = [w for w in ws if (w[0] + w[2]) / 2 >= g]
        if spans_gutter or not (lw and rw) and not seen_split and (min(w[0] for w in ws) < g < max(w[2] for w in ws)):
            (bottom if seen_split else top).append(l)
            continue
        seen_split = True
        if lw:
            left.append(Line(lw, pno, 1))
        if rw:
            right.append(Line(rw, pno, 2))
    return top + left + right + bottom


def norm_key(text):
    t = text.lower()
    t = re.sub(r"\d+", "", t)
    t = re.sub(r"\b[ivxlcdm]+\b", "", t)
    t = re.sub(r"[^a-z]+", " ", t).split()
    # OCR often misreads page numbers as letters ("lO", "i6"): drop tiny tokens at the ends
    while t and len(t[0]) <= 2:
        t.pop(0)
    while t and len(t[-1]) <= 2:
        t.pop()
    return " ".join(t)


def has_page_token(text):
    t = text.split()
    return bool(t) and (PAGE_TOKEN.match(t[0]) is not None or PAGE_TOKEN.match(t[-1]) is not None)


def is_page_number(text):
    t = text.strip().strip("[]()-–— .")
    t = re.sub(r"^(page|p\.)\s*", "", t, flags=re.I)
    return bool(t) and (t.isdigit() or bool(ROMAN.match(t))) and len(t) <= 6


def junk_ratio(text):
    toks = text.split()
    if not toks:
        return 0.0
    bad = 0
    for t in toks:
        core = t.strip(".,;:!?\"'()[]“”‘’-")
        if not core:
            continue
        if not re.fullmatch(r"[A-Za-zÀ-ſ'’-]+|\d[\d,.]*|[\d]+(st|nd|rd|th|s)?", core) or \
                (core.isalpha() and len(core) > 2 and not re.search(r"[aeiouyAEIOUY]", core)):
            bad += 1
    return round(bad / len(toks), 2)


def parse_pages(spec, n):
    """'11-16,18-133' -> sorted zero-based page indices; empty spec means every page."""
    if not spec:
        return list(range(n))
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        a, b = int(a), int(b) if b else int(a)
        out.update(p for p in range(a - 1, min(b, n)) if p >= 0)
    return sorted(out)


def pages_to_str(pages_1based):
    pages = sorted(set(pages_1based))
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(f"{pages[i]}-{pages[j]}" if j > i else f"{pages[i]}")
        i = j + 1
    return ",".join(out)

# ----------------------------------------------------------------------------
# pages: words from a born-digital PDF or from OCR, with two-page spreads split
# ----------------------------------------------------------------------------

@dataclass
class PageData:
    width: float
    height: float
    words: list           # (x0, y0, x1, y1, text)
    sups: list = field(default_factory=list)
    source_page: int = 0  # 1-based page number in the original PDF
    half: str = ""        # "left" or "right" for a split spread
    ocr: bool = False     # words came from OCR (line heights need normalising)


def split_spread(pd: PageData) -> list[PageData]:
    """A landscape page holding two book pages side by side becomes two pages."""
    if pd.width < pd.height * 1.15 or len(pd.words) < 80:
        return [pd]
    xs = [w for w in pd.words if w[4].strip()]
    body = [w for w in xs if pd.height * 0.12 < w[1] < pd.height * 0.9]  # ignore headers and footers
    best, best_x = None, None
    for x in range(int(pd.width * 0.4), int(pd.width * 0.6), 2):
        c = sum(1 for w in body if w[0] < x < w[2])
        if best is None or c < best:
            best, best_x = c, x
    left = [w for w in xs if (w[0] + w[2]) / 2 < best_x]
    right = [w for w in xs if (w[0] + w[2]) / 2 >= best_x]
    if best > max(3, len(body) * 0.04) or len(left) < len(xs) * 0.15 or len(right) < len(xs) * 0.15:
        return [pd]
    shifted = [(w[0] - best_x, w[1], w[2] - best_x, w[3], w[4]) for w in right]
    rsups = [((b[0] - best_x, b[1], b[2] - best_x, b[3]), t) for b, t in pd.sups if b[0] >= best_x]
    lsups = [(b, t) for b, t in pd.sups if b[0] < best_x]
    return [PageData(best_x, pd.height, left, lsups, pd.source_page, "left", pd.ocr),
            PageData(pd.width - best_x, pd.height, shifted, rsups, pd.source_page, "right", pd.ocr)]


class PageSet:
    """Every page's words, plus the PDF's metadata and bookmarks (mapped onto split pages)."""

    def __init__(self, path, pages: list[PageData], ocr_stats: dict | None = None):
        self.path = str(path)
        doc = pymupdf.open(path)
        self.title, self.author = metadata(doc, path)
        split = []
        for pd in pages:
            split += split_spread(pd)
        for pd in split:
            if pd.ocr:
                pd.words = normalise_ocr_heights(pd.words)
        self.pages = split
        self.spreads = sum(1 for p in split if p.half == "left")
        first = {}
        for i, p in enumerate(split):
            first.setdefault(p.source_page, i + 1)
        self.toc = [[lvl, t, first.get(pg, pg)] for lvl, t, pg in doc.get_toc()]
        self.ocr_stats = ocr_stats

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def __getitem__(self, i) -> PageData:
        return self.pages[i]

    @classmethod
    def from_pdf(cls, path) -> "PageSet":
        doc = pymupdf.open(path)
        pages = [PageData(pg.rect.width, pg.rect.height, [tuple(w[:5]) for w in pg.get_text("words")],
                          superscript_spans(pg), i + 1) for i, pg in enumerate(doc)]
        return cls(path, pages)

    @classmethod
    def from_ocr(cls, path, ocr: dict) -> "PageSet":
        pages = [PageData(p["width"], p["height"], [tuple(w) for w in p["words"]], [], i + 1, "", bool(p.get("ocr")))
                 for i, p in enumerate(ocr["pages"])]
        return cls(path, pages, ocr.get("stats"))


def normalise_ocr_heights(words: list) -> list:
    """OCR line boxes vary in height by up to half for the same type size and overlap their neighbours, which
    breaks the gap tests below. A line that sits at the page's ordinary line pitch from a neighbour is body text:
    keep its centre and give it 0.8 of the pitch as height. Isolated lines (headings, headers) keep their own."""
    if len(words) < 10:
        return words
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((round(w[1], 1), round(w[3], 1)), []).append(w)
    centres = sorted((k[0] + k[1]) / 2 for k in lines)
    heights = sorted(k[1] - k[0] for k in lines)
    med_h = heights[len(heights) // 2]
    steps = [b - a for a, b in zip(centres, centres[1:]) if 0.4 * med_h < b - a < 2.5 * med_h]
    if len(steps) < 5:
        return words
    pitch = statistics.median(steps)
    cap = 0.8 * pitch
    out = []
    for (y0, y1), ws in lines.items():
        c = (y0 + y1) / 2
        near = min((abs(c - o) for o in centres if abs(c - o) > 0.3 * pitch), default=99 * pitch)
        if y1 - y0 > cap and near <= 1.35 * pitch:
            y0, y1 = c - cap / 2, c + cap / 2
        out += [(w[0], y0, w[2], y1, w[4]) for w in ws]
    return out


# ----------------------------------------------------------------------------
# page furniture
# ----------------------------------------------------------------------------

@dataclass
class Layout:
    kept: list            # (Line, page width)
    header_text: dict     # page index -> removed running header
    body_h: float
    stats: Stats
    page_count: int
    pages: list


def analyse(ps: PageSet, pages, keep_footnotes=False) -> Layout:
    stats = Stats()
    all_lines, heights = {}, []
    for pno in pages:
        pd = ps[pno]
        ls = page_lines(pd, pno, stats)
        all_lines[pno] = (ls, pd.height, pd.width)
        heights += [l.h for l in ls]
    text_pages = sum(1 for p in pages if all_lines[p][0])
    if text_pages < max(1, len(pages) * 0.5) or not heights:
        raise ValueError(f"Only {text_pages} of {len(pages)} pages have text; this PDF needs OCR.")
    body_h = statistics.median(heights)

    top_keys, bot_keys = collections.Counter(), collections.Counter()
    key_heights = collections.defaultdict(list)

    def isolated_top(ls, i):
        return i + 1 >= len(ls) or ls[i + 1].y0 - ls[i].y1 > body_h * 0.6

    def isolated_bot(ls, i):
        return i == 0 or ls[i].y0 - ls[i - 1].y1 > body_h * 0.6

    for pno in pages:
        ls, H, _ = all_lines[pno]
        for i, l in enumerate(ls[:2]):
            if l.y0 < H * 0.12 and isolated_top(ls, i):
                k = norm_key(l.text)
                if k:
                    top_keys[k] += 1
                    key_heights[k].append(l.h)
        for i in range(max(0, len(ls) - 2), len(ls)):
            l = ls[i]
            if l.y1 > H * 0.88 and l.h < body_h * 1.6 and isolated_bot(ls, i):
                k = norm_key(l.text)
                if k:
                    bot_keys[k] += 1
    thresh = 3
    header_keys = {k for k, c in top_keys.items() if c >= thresh}
    footer_keys = {k for k, c in bot_keys.items() if c >= thresh}
    _hm = {}

    def header_match(k):
        if not k:
            return False
        if k in header_keys:
            return True
        if k not in _hm:
            _hm[k] = any(difflib.SequenceMatcher(None, k, hk).ratio() >= 0.85 for hk in header_keys)
        return _hm[k]

    def like_header(cand):
        return any(difflib.SequenceMatcher(None, cand, hk.replace(" ", "")).ratio() >= 0.6 for hk in header_keys)

    removed = stats.removed
    header_text, kept = {}, []
    for pno in pages:
        ls, H, W = all_lines[pno]
        # footnotes: the trailing run of small-type lines at the bottom of the page
        foot_start = len(ls)
        if not keep_footnotes:
            j = len(ls) - 1
            while j >= 0 and (is_page_number(ls[j].text) or norm_key(ls[j].text) in footer_keys) and ls[j].y1 > H * 0.85:
                j -= 1
            k = j
            while k >= 0 and ls[k].h < body_h * 0.84:
                k -= 1
            if k < j:
                foot_start = k + 1
        for i, l in enumerate(ls):
            in_top = l.y0 < H * 0.12 and i < 3
            in_bot = l.y1 > H * 0.88 and i >= len(ls) - 3
            if (in_top or in_bot) and is_page_number(l.text):
                removed["page number"] += 1
                continue
            k = norm_key(l.text)
            if (in_top and isolated_top(ls, i) and header_match(k)
                    and (has_page_token(l.text) or l.h < statistics.median(key_heights.get(k) or [l.h]) * 1.3)):
                removed["header: " + l.text[:50]] += 1
                header_text.setdefault(pno, l.text)
                continue
            if in_bot and norm_key(l.text) in footer_keys and isolated_bot(ls, i):
                removed["footer: " + l.text[:50]] += 1
                continue
            if in_top and i < 2 and header_keys and l.h < body_h * 1.3:
                # running header glued to the first text line: "192 BATILCESTUPDIRS The column..."
                ws0 = sorted(l.words, key=lambda w: w[0])
                toks = [w[4] for w in ws0]
                j = 1 if toks and PAGE_TOKEN.match(toks[0]) else 0
                caps = j
                while caps < len(toks) and caps - j < 5 and re.fullmatch(r"[A-ZÀ-Þ'’.:,|/-]{2,}", toks[caps]):
                    caps += 1
                if caps > j:
                    cand = norm_key(" ".join(toks[j:caps])).replace(" ", "")
                    if caps < len(toks) and j == 0 and PAGE_TOKEN.match(toks[caps]):
                        caps += 1   # "MILITARY INSTITUTIONS 219 ...": page number after the header
                    rest = ws0[caps:]
                    has_num = j == 1 or (caps > 0 and PAGE_TOKEN.match(toks[caps - 1]) is not None)
                    if not rest and has_num and cand and like_header(cand):
                        removed["header (misspelled): " + l.text[:50]] += 1
                        continue
                    if rest and cand and like_header(cand):
                        removed["header (glued): " + " ".join(toks[:caps])[:50]] += 1
                        header_text.setdefault(pno, " ".join(toks[:caps]))
                        l = Line(list(rest), pno, l.col)
            if i >= foot_start and not ((in_top or in_bot) and is_page_number(l.text)):
                removed["footnote line"] += 1
                continue
            # superscript note numbers: small digit-only words
            ws = [w for w in l.words if not (re.fullmatch(r"[\d,\*†‡]+", w[4]) and (w[3] - w[1]) < l.h * 0.75)]
            if len(ws) != len(l.words):
                removed["note marker"] += len(l.words) - len(ws)
                if not ws:
                    continue
                l = Line(ws, pno, l.col)
            kept.append((l, W))
    return Layout(kept, header_text, body_h, stats, ps.page_count, pages)


# ----------------------------------------------------------------------------
# dossier for the AI
# ----------------------------------------------------------------------------

def metadata(doc, path) -> tuple[str, str]:
    import os
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip() or os.path.splitext(os.path.basename(str(path)))[0]
    return title, (meta.get("author") or "").strip()


def dossier(ps: PageSet) -> dict:
    """Per-page summary: removed running header, words, junk ratio, first lines, heading-like lines."""
    pages = list(range(ps.page_count))
    lay = analyse(ps, pages)
    title, author = ps.title, ps.author
    by_page = collections.defaultdict(list)
    for l, _ in lay.kept:
        by_page[l.page].append(l)
    out = {"page_count": ps.page_count, "title": title, "author": author, "spreads_split": ps.spreads,
           "bookmarks": ps.toc[:200], "pages": []}
    for pno in pages:
        ls = by_page.get(pno, [])
        text = " ".join(l.text for l in ls)
        heads = []
        for l in ls:
            n = len(l.text.split())
            caps = sum(c.isupper() for c in l.text) >= max(1, sum(c.islower() for c in l.text))
            if n <= 12 and (l.h >= lay.body_h * 1.2 or CHAPTERISH.match(l.text.strip()) or (caps and n <= 10 and len(l.text) > 3)):
                heads.append(l.text)
        out["pages"].append({"page": pno + 1, "running_header": lay.header_text.get(pno, ""),
                             "words": len(text.split()), "junk": junk_ratio(text),
                             "first_lines": [l.text[:90] for l in ls[:3]], "heading_like": heads[:6]})
    return out


# ----------------------------------------------------------------------------
# conversion
# ----------------------------------------------------------------------------

def locate_chapters(specs, kept):
    """Map explicit chapter starts (page + heading text) onto extracted lines."""
    if not specs:
        return {}, set(), []
    by_page = collections.defaultdict(list)
    for i, (l, _) in enumerate(kept):
        by_page[l.page + 1].append(i)
    starts, skip, log = {}, set(), []

    def nk(t):
        return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

    for sp in specs:
        pg, match, nlines, title = int(sp["page"]), sp.get("match") or "", int(sp.get("lines", 1) or 0), sp["title"]
        cand = [i for i in by_page.get(pg, []) if i not in starts]
        if not cand:
            later = [i for p in sorted(by_page) if p > pg for i in by_page[p] if i not in starts]
            cand = later[:1]
            if not cand:
                log.append({"page": pg, "title": title, "found": False, "note": "page not in selection"})
                continue
        best, best_r = None, 0.0
        if match:
            for i in cand:
                r = difflib.SequenceMatcher(None, nk(kept[i][0].text), nk(match)).ratio()
                if nk(match) and nk(kept[i][0].text).startswith(nk(match)[:12]):
                    r = max(r, 0.9)
                if r > best_r:
                    best, best_r = i, r
        found = best is not None and best_r >= 0.6
        i0 = best if found else cand[0]
        starts[i0] = title
        if found:
            page_of = kept[i0][0].page
            for k in range(i0, i0 + max(0, nlines)):
                if k < len(kept) and kept[k][0].page == page_of:
                    skip.add(k)
        log.append({"page": pg, "title": title, "found": found,
                    "matched_line": kept[i0][0].text[:80], "score": round(best_r, 2)})
    return starts, skip, log


def bookmarks_for(toc, pages, toc_level=None):
    """Bookmarks as a flat chapter list over the kept pages. Uses parts and chapters (two levels) when the book
    has them; a bookmark whose page was cut (a near-empty part title page, say) moves to the next kept page."""
    if not toc:
        return []
    if toc_level is None:
        toc_level = 2 if sum(1 for t in toc if t[0] <= 2) <= 120 else 1
    kept = sorted(pages)
    out = []
    for lvl, title, pg in toc:
        if lvl > toc_level:
            continue
        nxt = next((p for p in kept if p >= pg - 1), None)
        if nxt is not None:
            out.append([lvl, title, nxt + 1])
    return out


def convert(ps: PageSet, pages_spec=None, chapters=None, title=None, author=None, keep_footnotes=False,
            heading_ratio=1.35, toc_level=None) -> tuple[Book, dict]:
    """The book from the kept pages, split into sections. Returns (book, build info for the QC report)."""
    pages = parse_pages(pages_spec, ps.page_count)
    if not pages:
        raise ValueError("No pages selected.")
    lay = analyse(ps, pages, keep_footnotes)
    kept, body_h = lay.kept, lay.body_h

    xs, ws_ = collections.defaultdict(list), collections.defaultdict(list)
    for l, W in kept:
        xs[(l.page, l.col)].append(l.x0)
        if l.h < body_h * 1.3:
            ws_[(l.page, l.col)].append(l.x1 - l.x0)
    left_margin = {k: statistics.median(sorted(v)[: max(1, len(v) // 2)]) for k, v in xs.items()}
    col_w = {k: sorted(v)[int(len(v) * 0.8)] for k, v in ws_.items() if v}

    paras, prev = [], None
    starts, skip_idx, match_log = locate_chapters(chapters, kept)
    boundaries = []
    for li, (l, W) in enumerate(kept):
        if li in starts:
            boundaries.append((len(paras), starts[li]))
            prev = None
        if li in skip_idx:
            continue
        heading = l.h >= body_h * heading_ratio and len(l.text.split()) <= 14
        if prev is None:
            new_para = True
        elif heading or prev["heading"]:
            new_para = not (heading and prev["heading"] and l.page == prev["page"]
                            and abs(l.h - prev["h"]) < body_h * 0.4 and l.y0 - prev["y1"] < l.h * 1.2)
        else:
            indent = (l.x0 - left_margin.get((l.page, l.col), l.x0)) > W * 0.015
            pw = col_w.get((prev["page"], prev["col"]), prev["x1"] - prev["x0"])
            short_prev = (prev["x1"] - prev["x0"]) < pw * 0.85 and prev["text"].rstrip().endswith(TERMINAL)
            if l.page != prev["page"] or l.col != prev["col"]:
                # page or column break: continue the paragraph unless the last one clearly ended
                new_para = prev["text"].rstrip().endswith(TERMINAL) and (indent or short_prev)
            else:
                new_para = l.y0 - prev["y1"] > body_h * 0.9 or indent or short_prev
        if new_para:
            prev = {"text": l.text, "page": l.page, "h": l.h, "heading": heading,
                    "y1": l.y1, "x0": l.x0, "x1": l.x1, "col": l.col}
            paras.append(prev)
        else:
            t = prev["text"]
            if heading and prev["heading"]:
                prev["text"] = t.rstrip(".") + ". " + l.text
            elif re.search(r"[A-Za-z]-$", t) and re.match(r"[a-z]", l.text):
                prev["text"] = t[:-1] + l.text
            else:
                prev["text"] = t + " " + l.text
            prev.update(y1=l.y1, x0=l.x0, x1=l.x1, page=l.page, col=l.col)
    for p in paras:
        p["text"] = re.sub(r"\s+", " ", p["text"]).strip()

    title, author = title or ps.title, author or ps.author
    sections: list[tuple[str, list, bool]] = []   # (title, paras, explicit)

    def fuzzy(a, b):
        a, b = norm_key(a), norm_key(b)
        return bool(a) and bool(b) and (a.startswith(b[:25]) or b.startswith(a[:25]) or b[:25] in a)

    toc = [] if chapters else bookmarks_for(ps.toc, pages, toc_level)
    source = "explicit list" if chapters else "bookmarks" if toc else "large headings"
    if chapters:
        if boundaries and boundaries[0][0] > 0:
            sections.append(("Title Page", paras[: boundaries[0][0]], False))
        elif not boundaries:
            sections.append((title, paras, False))
        for j, (i, t) in enumerate(boundaries):
            end = boundaries[j + 1][0] if j + 1 < len(boundaries) else len(paras)
            sections.append((t, paras[i:end], True))
    elif toc:
        st, used = [], set()
        idx_by_page = collections.defaultdict(list)
        for i, p in enumerate(paras):
            idx_by_page[p["page"]].append(i)
        for _lvl, t, pg in toc:
            pno = pg - 1
            cand = [i for i in idx_by_page.get(pno, []) if i not in used]
            if not cand:
                cand = [i for i in range(len(paras)) if paras[i]["page"] > pno and i not in used][:1]
            if not cand:
                continue
            match = next((i for i in cand if paras[i]["heading"] and fuzzy(paras[i]["text"], t)), None)
            if match is None:
                match = next((i for i in cand if fuzzy(paras[i]["text"], t)), cand[0])
            used.add(match)
            st.append((match, t))
        st.sort()
        if st and st[0][0] > 0:
            sections.append(("Title Page", paras[: st[0][0]], False))
        for j, (i, t) in enumerate(st):
            end = st[j + 1][0] if j + 1 < len(st) else len(paras)
            sections.append((t, paras[i:end], False))
    else:
        cur_title, cur = "Title Page", []
        for p in paras:
            if p["heading"] and cur and any(not q["heading"] for q in cur):
                sections.append((cur_title, cur, False))
                cur_title, cur = p["text"], []
            elif p["heading"] and not any(not q["heading"] for q in cur):
                cur_title = p["text"] if cur_title == "Title Page" and not cur else (cur_title + ". " + p["text"] if cur else p["text"])
            cur.append(p)
        if cur:
            sections.append((cur_title, cur, False))

    book = Book(title=title, author=author, source=ps.path, kind="pdf")
    for t, ps, explicit in sections:
        if not ps and not explicit:
            continue
        heading, body = "", ps
        if ps and ps[0]["heading"] and not explicit:
            hs = []
            for p in ps:
                if not p["heading"]:
                    break
                hs.append(p["text"])
            heading, body = ". ".join(hs), ps[len(hs):]
        first = body[0]["page"] + 1 if body else (ps[0]["page"] + 1 if ps else 0)
        book.sections.append(Section(title=t, paragraphs=[p["text"] for p in body], source=f"p{first}",
                                     label=t, heading=heading))
    removed = lay.stats.removed
    build = {"sections": [{"title": s.title, "words": s.words} for s in book.sections],
             "removed": dict(removed), "note_markers": removed["note marker"] + lay.stats.markers,
             "two_column_pages": lay.stats.two_col, "chapter_matches": match_log, "chapters_from": source,
             "pages_kept": pages_to_str([p + 1 for p in pages])}
    return book, build


def cover_image(path, out_path) -> str | None:
    """The first page rendered as the cover image."""
    try:
        doc = pymupdf.open(path)
        pix = doc[0].get_pixmap(dpi=110)
        pix.save(str(out_path))
        return str(out_path)
    except Exception:
        return None
