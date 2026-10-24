"""OCR for scanned PDFs without Homebrew: Apple's Vision framework, with PyMuPDF's built-in Tesseract as fallback.

OCR produces word boxes (x0, y0, x1, y1, text) in page points for every page. They are saved as JSON in the
book's work folder and read by ingest/pdf.py exactly like the words of a born-digital PDF, so the ported layout
code (headers, footers, footnotes by line height, two columns, chapter matching) is the same for both.
"""
from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf

DPI = 300


def needs_ocr(path) -> tuple[bool, int, int]:
    """(OCR needed, pages with a text layer, total pages): needed when under half the pages have text."""
    doc = pymupdf.open(path)
    with_text = sum(1 for pg in doc if len(pg.get_text().strip()) > 40)
    return with_text < doc.page_count * 0.5, with_text, doc.page_count


def needs_reocr(dossier: dict) -> tuple[bool, int, int]:
    """The existing text layer looks garbled: more than 20% of text pages with junk over 0.25."""
    junky = sum(1 for p in dossier["pages"] if p["words"] > 80 and p["junk"] > 0.25)
    text_pages = sum(1 for p in dossier["pages"] if p["words"] > 80) or 1
    return junky > text_pages * 0.2, junky, text_pages


# ----------------------------------------------------------------------------
# engines: words as (x0, y0, x1, y1, text) in page points, top-left origin
# ----------------------------------------------------------------------------

def vision_words(png: bytes, width_pt: float, height_pt: float) -> tuple[list, float]:
    import Vision
    from Foundation import NSData, NSMakeRange
    data = NSData.dataWithBytes_length_(png, len(png))
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(["en-US", "en-GB"])
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")
    words, confs = [], []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)[0]
        s = str(cand.string())
        confs.append(float(cand.confidence()))
        lb = obs.boundingBox()  # normalised, origin bottom-left
        ly0 = (1 - lb.origin.y - lb.size.height) * height_pt
        ly1 = (1 - lb.origin.y) * height_pt
        pos = 0
        for token in s.split(" "):
            if not token:
                pos += 1
                continue
            box = None
            try:
                rect_obs, _ = cand.boundingBoxForRange_error_(NSMakeRange(pos, len(token)), None)
                if rect_obs is not None:
                    box = rect_obs.boundingBox()
            except Exception:
                box = None
            if box is not None and box.size.width > 0:
                x0 = box.origin.x * width_pt
                x1 = (box.origin.x + box.size.width) * width_pt
            else:  # spread the line's width by character count
                x0 = (lb.origin.x + pos / max(1, len(s)) * lb.size.width) * width_pt
                x1 = x0 + len(token) / max(1, len(s)) * lb.size.width * width_pt
            words.append((round(x0, 2), round(ly0, 2), round(x1, 2), round(ly1, 2), token))
            pos += len(token) + 1
    return words, (statistics.median(confs) if confs else 0.0)


def tesseract_words(png: bytes, width_pt: float, height_pt: float, tessdata: str) -> tuple[list, float]:
    img = pymupdf.open("png", png)
    page = img[0]
    tp = page.get_textpage_ocr(dpi=DPI, full=True, tessdata=tessdata)
    sx, sy = width_pt / page.rect.width, height_pt / page.rect.height
    words = [(w[0] * sx, w[1] * sy, w[2] * sx, w[3] * sy, w[4]) for w in page.get_text("words", textpage=tp)]
    return words, 1.0 if words else 0.0


def tessdata_dir() -> str | None:
    from .. import paths
    d = paths.runtime_dir() / "tessdata"
    return str(d) if (d / "eng.traineddata").exists() else None


def ocr_available(engine: str) -> bool:
    if engine == "tesseract":
        return tessdata_dir() is not None
    try:
        import Vision  # noqa: F401
        return True
    except Exception:
        return False


def default_engine() -> str:
    return "vision" if ocr_available("vision") else "tesseract"


def _ocr_page(path: str, i: int, engine: str, tess: str | None) -> dict:
    page = pymupdf.open(path)[i]
    w, h = page.rect.width, page.rect.height
    best = None
    for rot in (0, 90, 180, 270):
        mat = pymupdf.Matrix(DPI / 72, DPI / 72).prerotate(rot)
        png = page.get_pixmap(matrix=mat).tobytes("png")
        pw, ph = (w, h) if rot in (0, 180) else (h, w)
        words, conf = tesseract_words(png, pw, ph, tess) if engine == "tesseract" else vision_words(png, pw, ph)
        score = conf * sum(len(t[4]) for t in words)
        if best is None or score > best[0]:
            best = (score, words, pw, ph, rot, conf)
        if rot == 0 and (not words or (conf >= 0.5 and len(words) >= 5)):
            break  # upright and confident, or a blank page: no need to try other orientations
    _, words, pw, ph, rot, conf = best
    return {"width": pw, "height": ph, "words": [list(x) for x in words], "rotated": rot, "confidence": conf,
            "ocr": True}


# ----------------------------------------------------------------------------
# drop caps: a large decorative initial ("T" spanning three lines) is usually missing from OCR, leaving "he
# hydrogen bomb". Its trace is a first line that starts lowercase and is indented to make room for the letter.
# That gap is recognised again on its own, and the letter found there is put back on the word.
# ----------------------------------------------------------------------------

def _lines(words):
    """OCR lines: words sharing a line box, split where a wide gap shows two columns or facing pages."""
    groups: dict[tuple, list] = {}
    for w in words:
        groups.setdefault((round(w[1], 1), round(w[3], 1)), []).append(w)
    out = []
    for ws in groups.values():
        ws = sorted(ws, key=lambda w: w[0])
        h = ws[0][3] - ws[0][1]
        cur = [ws[0]]
        for a, b in zip(ws, ws[1:]):
            if b[0] - a[2] > 3 * h:
                out.append(cur)
                cur = []
            cur.append(b)
        out.append(cur)
    return sorted(out, key=lambda ws: (ws[0][1], ws[0][0]))


def drop_cap_candidates(words) -> list[tuple[int, tuple]]:
    """(index of the word in `words`, crop box in points) for lines that look like they lost a drop cap."""
    lines = _lines(words)
    out = []
    for i, ln in enumerate(lines):
        first = ln[0]
        t = first[4]
        if not t[:1].isalpha() or not t[:1].islower():
            continue
        h = first[3] - first[1]

        def same_column(l):  # on a two-page spread, ignore lines that belong to the facing page
            return first[0] - 8 * h <= l[0][0] <= first[0] + 2 * h

        col = [l for l in lines if same_column(l)]
        j = next(k for k, l in enumerate(col) if l is ln)
        # the capital can span three lines, all indented: the margin shows in the lines after it
        below = [l[0][0] for l in col[j + 1:j + 7] if 0 < l[0][1] - first[1] < 8 * h]
        if len(below) < 2:
            continue
        margin = min(below)
        indent = first[0] - margin
        if indent < 1.2 * h or indent > 8 * h:
            continue
        above = [l for l in col[:j] if 0 <= first[1] - l[0][3] < 1.2 * h]
        if above and any(l[0][0] <= margin + h for l in above):
            continue  # a normal line sits right above: this is not the start of a section
        box = (margin - 0.5 * h, first[1] - 0.6 * h, first[0] - 0.1 * h, first[1] + 3.6 * h)
        out.append((words.index(first), box))
    return out


def merged_cap(words, wi: int) -> int | None:
    """Vision sometimes reads the capital as the start of the next line's first word ("Tray" for "T" + "ray"),
    in an unusually tall box left of the indented word. Returns that word's index, or None."""
    first = words[wi]
    h = first[3] - first[1]
    for k, w in enumerate(words):
        if (k != wi and w[4][:1].isupper() and 0 < w[1] - first[1] < 1.5 * h and w[0] < first[0]
                and (w[3] - w[1]) > 1.8 * h and len(w[4]) > 1):
            return k
    return None


def _single_letter(png: bytes) -> str | None:
    import Vision
    from Foundation import NSData
    data = NSData.dataWithBytes_length_(png, len(png))
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return None
    for obs in req.results() or []:
        t = str(obs.topCandidates_(1)[0].string()).strip()
        if len(t) == 1 and t.isalpha() and t.isupper():
            return t
    return None


def _is_word(token: str) -> bool:
    """The letter must turn the fragment into a real word ("he" -> "The"), never into "Ahe" or "Cstrategy"."""
    from ..prepare.audit import dictionary, known_word
    core = token.strip(".,;:!?'\"()\u2019\u201d").lower()
    return core.isalpha() and core.isascii() and known_word(core, dictionary())


def restore_drop_caps(src, data: dict) -> int:
    """Put recovered initials back on OCR'd pages. Returns how many were restored."""
    doc = pymupdf.open(src)
    restored = 0
    for i, page in enumerate(data["pages"]):
        if not page.get("ocr") or page.get("rotated"):
            continue
        words = [list(w) for w in page["words"]]
        for wi, (x0, y0, x1, y1) in drop_cap_candidates([tuple(w) for w in words]):
            k = merged_cap([tuple(w) for w in words], wi)
            if k is not None and _is_word(words[k][4][0] + words[wi][4]) and _is_word(words[k][4][1:]):
                words[wi][4] = words[k][4][0] + words[wi][4]
                words[wi][0] = words[k][0]
                words[k][4] = words[k][4][1:]
                words[k][0] = words[wi][0] + (words[k][2] - words[k][0]) / max(1, len(words[k][4]) + 1)
                restored += 1
                continue
            letter = None
            h = (y1 - y0) / 4.2
            # tight around the gap first; then reaching into the blank margin, since a letter that touches the
            # edge of the image is sometimes not read
            for rx0, ry0, rx1, ry1 in ((x0, y0, x1, y1), (x0 - 2.5 * h, y0 - h, x1, y1 + 0.5 * h)):
                rect = pymupdf.Rect(max(0, rx0), max(0, ry0), rx1, ry1)
                if rect.is_empty or rect.width < 4:
                    continue
                letter = _single_letter(doc[i].get_pixmap(dpi=400, clip=rect).tobytes("png"))
                if letter and _is_word(letter + words[wi][4]):
                    break
            if letter and _is_word(letter + words[wi][4]):
                words[wi][4] = letter + words[wi][4]
                words[wi][0] = x0
                restored += 1
        page["words"] = words
    return restored


def ocr_words(src, out_json, engine: str = "vision", force: bool = False, workers: int = 4,
              progress: Callable[[int, int], None] | None = None) -> dict:
    """OCR every page without a usable text layer (or every page if `force`); pages that have text keep it.
    Saves {"pages": [...], "stats": {...}} to `out_json` and returns it. Reuses a finished file."""
    out_json = Path(out_json)
    if out_json.exists():
        data = json.loads(out_json.read_text(encoding="utf-8"))
        if data.get("stats", {}).get("engine") == engine and data.get("stats", {}).get("forced") == force:
            if "drop_caps" not in data["stats"] and engine == "vision":
                data["stats"]["drop_caps"] = restore_drop_caps(src, data)
                out_json.write_text(json.dumps(data), encoding="utf-8")
            return data
    tess = tessdata_dir()
    if engine == "tesseract" and not tess:
        raise RuntimeError("Tesseract language data is not installed. Run `fiatvox doctor`.")
    doc = pymupdf.open(src)
    n = doc.page_count
    pages: list[dict | None] = [None] * n
    todo = []
    for i, page in enumerate(doc):
        if not force and len(page.get_text().strip()) > 40:
            pages[i] = {"width": page.rect.width, "height": page.rect.height,
                        "words": [list(w[:5]) for w in page.get_text("words")], "rotated": 0, "confidence": 1.0,
                        "ocr": False}
        else:
            todo.append(i)
    done = n - len(todo)
    if progress:
        progress(done, n)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {i: ex.submit(_ocr_page, str(src), i, engine, tess) for i in todo}
        for i in todo:
            pages[i] = futs[i].result()
            done += 1
            if progress:
                progress(done, n)
    stats = {"engine": engine, "forced": force, "pages_ocrd": len(todo), "pages": n,
             "rotated": sum(1 for p in pages if p["rotated"]),
             "low_confidence_pages": [i + 1 for i, p in enumerate(pages) if p["ocr"] and p["words"]
                                      and p["confidence"] < 0.4]}
    data = {"pages": pages, "stats": stats}
    if engine == "vision":
        stats["drop_caps"] = restore_drop_caps(src, data)
    tmp = out_json.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(out_json)
    return data
