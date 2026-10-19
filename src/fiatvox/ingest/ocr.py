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


def ocr_words(src, out_json, engine: str = "vision", force: bool = False, workers: int = 4,
              progress: Callable[[int, int], None] | None = None) -> dict:
    """OCR every page without a usable text layer (or every page if `force`); pages that have text keep it.
    Saves {"pages": [...], "stats": {...}} to `out_json` and returns it. Reuses a finished file."""
    out_json = Path(out_json)
    if out_json.exists():
        data = json.loads(out_json.read_text(encoding="utf-8"))
        if data.get("stats", {}).get("engine") == engine and data.get("stats", {}).get("forced") == force:
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
    tmp = out_json.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(out_json)
    return data
