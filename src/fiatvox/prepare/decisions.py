"""What to keep, where chapters start, what they are called: prompts for the AI, local heuristics without it, and
validation of whatever comes back. Ported from audiobook.py. The AI never edits the book; it returns JSON."""
from __future__ import annotations

import copy
import json

from ..ingest.pdf import pages_to_str, parse_pages
from ..model import Book
from .clean import FLAGS

COMMON_RULES = """
KEEP: the main title page only if its text is legible; dedication; epigraphs; preface, foreword, introduction
(by author, editor or translator); translator's or editor's note; every part and chapter; conclusion, epilogue,
afterword; appendices that are prose the reader would want to hear.
CUT: covers; blank or near-blank pages; library stamps, bookplates, ownership marks, digitisation notices;
publisher adverts, "also by", praise and blurbs; copyright and cataloguing pages; tables of contents; lists of
illustrations, maps, plates or tables; handwritten, facsimile or foreign-language image pages (high junk ratio);
maps, diagrams, plates and their captions; endnotes and footnote sections; bibliography and references; index;
back cover.

Flags (turn one on only when the evidence supports it):
- fix_ligatures: words split after fi/fl ("fi ction", "offi cer")
- strip_glued_numbers: note numbers glued to words or punctuation ("war.23 The")
- strip_citations: parenthetical author-year citations "(Smith 2019, 45)"

Replacements: exact, case-sensitive [find, replace] pairs for garbled words you can see in the dossier that the
narrator would read wrongly, for example an OCR-garbled name in a heading or byline ("Simonps" -> "Simonds").
Do not add pronunciation respellings, do not modernise spelling, and do not include chapter headings you
already retitled.

Titles are what the narrator will speak at the start of each section, and they become the chapter names the
listener sees in the audiobook player. Write them in normal sentence or title case, with numbers as words and a
colon after the label: "Chapter Three: Analysis of the Battle of Cannae", "Part Two: Modern Battle", "Preface",
"Translators' Note", "Appendix Two: Historical Documents". Every numbered chapter must be titled in this form,
"Chapter <number as a word>: <chapter title>", numbered in one sequence through the whole book even when the
book restarts its numbering in each part. Never include body text in a title.
"""

PDF_PROMPT = """You are preparing a book PDF for text-to-speech narration. Below is a page-by-page dossier produced
by software that already removed running headers, page numbers and footnotes. For each page it gives: page
number, the running header it removed (the book's own chapter label, useful evidence of where chapters are
even when a heading is garbled), word count, junk ratio (share of garbled tokens; above about 0.3 usually
means an image, handwriting or a diagram), the first lines, and short heading-like lines. Headings from OCR
may be garbled ("CHAPTERGHE", "CHA PER I", "CHAPTER. LEE"). Scanned two-page spreads have already been split,
so page numbers here may not match the printed ones.

Decide:
1. "pages": the page ranges to KEEP, as a string like "11-16,18-22,27-133,135-299".
2. "chapters": every point where a new section begins (preface, introduction, part, chapter, appendix and so
   on), in reading order, each as {"page": N, "match": "heading line exactly as it appears in heading_like or
   first_lines", "lines": how many consecutive heading lines starting at match form the printed heading and
   should be replaced by the title (for example "CHAPTER. LEE" + "ANALYSIS OF THE BATTLE OF CANNAE" = 2; use 0
   if no heading line survives and you are placing the start at the first line of the page), "title": the
   spoken title}. Use the running headers and the table of contents page (even though it will be cut) to name
   chapters correctly and to find chapters whose headings were lost. A part heading directly followed by its
   first chapter on the same page may be one entry ("Part Two: Modern Battle. Chapter One: General
   Discussion").
3. "flags", "replacements", "title" (book title), "author", and "notes" (short strings explaining what you cut
   and why, and anything the human should check).
""" + COMMON_RULES + """
Return ONLY this JSON object, no commentary:
{"pages": "...", "chapters": [{"page": 0, "match": "", "lines": 1, "title": ""}], "flags": [], "replacements": [],
 "title": "", "author": "", "notes": []}

DOSSIER
"""

EPUB_PROMPT = """You are preparing an EPUB for text-to-speech narration. Below is a section-by-section dossier: section
number, file name, table-of-contents label, first heading, word count, the opening and closing text, and a
count of suspected OCR or conversion problems. Book-level audit counts and examples follow.

Decide:
1. "drop": section numbers to remove.
2. "titles": {section number: spoken title} for every kept section whose heading is missing, garbled, only a
   number, would read badly aloud, or is a numbered chapter, part or appendix not already written in the
   "Chapter Two: The History of Eggs" form described below. Leave other good headings alone.
3. "flags", "replacements", "title", "author", and "notes" (short strings: what you cut and why, and anything
   the human should check). Endnote sections split across several files often show odd first headings; drop
   them all.
""" + COMMON_RULES + """
Return ONLY this JSON object, no commentary:
{"drop": [], "titles": {}, "flags": [], "replacements": [], "title": "", "author": "", "notes": []}

DOSSIER
"""

REVIEW_PROMPT = """You earlier made structural decisions for turning a book into an audiobook. The book has now been
built and measured. Your job in this round: find problems a LISTENER would hear, and correct your decisions to fix
them. Only fix what the evidence below shows. Do not rewrite prose, modernise spelling, or alter the author's
style. Ignore flags that are real words, names, or foreign phrases.

Corrections you may return in "patch" (omit any you do not need):
- "add_replacements": [[find, replace], ...] exact, case-sensitive text copied from the snippets below. Use them for
  garbled words ("RectrAL" -> "Recital") and for leftover page furniture such as a running header or page number
  stuck into the text ("192 BATILCESTUPDIRS" -> ""). Include a neighbouring word in find when the garble alone might
  also match good text elsewhere.
- "remove_replacements": [find, ...] to undo an earlier replacement that caused harm.
- "pages" (PDF only): the complete corrected page string, only to restore wrongly cut content or cut junk that
  slipped through.
- "chapters" (PDF only): the complete corrected chapters list (same format as before), only if a chapter start
  or spoken title is wrong.
- "drop" (EPUB only): the complete corrected list of section numbers to remove.
- "titles" (EPUB only): {section number: spoken title} to add or fix; use "" to remove one.
- "flags": the complete corrected list.

If nothing a listener would notice remains, return {"done": true, "reasons": [], "patch": {}}.
If you fix things and believe nothing else will remain, set "done": true as well.
Return ONLY JSON: {"done": false, "reasons": ["what you fixed and the evidence"], "patch": {}}

"""

NOTE_PREAMBLE = """The listener has heard the first chapter and reports this problem, which takes priority over
everything else in this round:

    {note}

"""


# ----------------------------------------------------------------------------
# dossiers
# ----------------------------------------------------------------------------

def epub_dossier(book: Book, audit: dict) -> dict:
    out = {"sections": [], "audit": {k: audit[k] for k in ("words", "issues", "rate", "verdict", "counts", "examples")}}
    for n, s in enumerate(book.sections, 1):
        flat = " ".join(s.text.split())
        a = audit["sections"][n - 1]
        out["sections"].append({"n": n, "file": s.source, "toc_label": s.label, "heading": s.heading or s.title,
                                "words": s.words, "opening": flat[:350],
                                "ending": flat[-150:] if len(flat) > 500 else "", "issues": a["issues"],
                                "rate": a["rate"], "is_nav": s.is_nav})
    return out


def compact_pdf_dossier(d: dict) -> str:
    lines = [f"Title (metadata): {d.get('title')}   Author: {d.get('author')}   Pages: {d.get('page_count')}"]
    if d.get("bookmarks"):
        lines.append("Bookmarks: " + "; ".join(f"{t} (p{pg})" for _, t, pg in d["bookmarks"][:80]))
    for p in d["pages"]:
        first = " / ".join(p["first_lines"])
        heads = " | ".join(p["heading_like"])
        lines.append(f"p{p['page']} hdr=[{p['running_header'][:40]}] w={p['words']} junk={p['junk']} :: {first[:220]}"
                     + (f"  ## heading_like: {heads[:160]}" if heads else ""))
    return "\n".join(lines)


def compact_epub_dossier(d: dict) -> str:
    lines = []
    for s in d["sections"]:
        lines.append(("(THIS IS THE BUILT-IN TABLE OF CONTENTS; DROP IT) " if s.get("is_nav") else "")
                     + f"#{s['n']} [{s['file']}] toc=\"{s['toc_label'][:60]}\" heading=\"{s['heading'][:70]}\" "
                     f"words={s['words']} issues={s['issues']} :: {s['opening'][:300]}"
                     + (f" ... END: {s['ending']}" if s["ending"] else ""))
    a = d["audit"]
    lines.append(f"AUDIT: {a['issues']} issues in {a['words']} words ({a['rate']}/1k, {a['verdict']}); counts {a['counts']}")
    for k, v in a["examples"].items():
        lines.append(f"  {k}: {' | '.join(v[:10])}")
    return "\n".join(lines)


def prompt_for(kind: str, dossier: dict) -> str:
    if kind == "pdf":
        return PDF_PROMPT + compact_pdf_dossier(dossier)
    return EPUB_PROMPT + compact_epub_dossier(dossier)


# ----------------------------------------------------------------------------
# heuristics (no AI, or the AI failed)
# ----------------------------------------------------------------------------

FRONT_BACK = ("cover", "copyright", "contents", "index", "bibliograph", "also by", "acknowledg", "notes", "endnotes",
              "about the author", "praise for", "photo insert", "title page", "titlepage")


def heuristic(kind: str, dossier: dict) -> dict:
    if kind == "pdf":
        keep = [p["page"] for p in dossier["pages"] if p["words"] >= 15 and p["junk"] < 0.35]
        return {"pages": pages_to_str(keep), "chapters": None, "flags": [], "replacements": [],
                "title": dossier.get("title"), "author": dossier.get("author"),
                "notes": ["No AI: kept pages with readable text; chapters from bookmarks or large headings."]}
    drop = []
    for s in dossier["sections"]:
        probe = (s["toc_label"] + " " + s["heading"] + " " + s["file"]).lower()
        if s.get("is_nav") or s["words"] < 5 and not s["heading"] or any(w in probe for w in FRONT_BACK):
            drop.append(s["n"])
    flags = ["fix_ligatures"] if dossier["audit"]["counts"].get("split ligature", 0) >= 3 else []
    return {"drop": drop, "titles": {}, "flags": flags, "replacements": [], "title": "", "author": "",
            "notes": ["No AI: dropped sections whose titles look like front or back matter."]}


# ----------------------------------------------------------------------------
# validation of what the AI returned
# ----------------------------------------------------------------------------

def sanitise(kind: str, d: dict, dossier: dict) -> tuple[dict, list[str]]:
    """Keep only well-formed fields; return the cleaned decisions and warnings about what was thrown away."""
    warn, out = [], {}
    out["flags"] = [f for f in (d.get("flags") or []) if f in FLAGS]
    reps = []
    for p in d.get("replacements") or []:
        if isinstance(p, (list, tuple)) and len(p) == 2 and str(p[0]).strip() and len(str(p[0])) <= 120:
            reps.append([str(p[0]), str(p[1])])
        else:
            warn.append(f"ignored a malformed replacement: {str(p)[:60]}")
    out["replacements"] = reps
    out["title"] = str(d.get("title") or "").strip()
    out["author"] = str(d.get("author") or "").strip()
    out["notes"] = [str(x) for x in (d.get("notes") or [])][:30]
    if kind == "pdf":
        n = dossier.get("page_count") or len(dossier["pages"])
        try:
            keep = parse_pages(d.get("pages"), n)
        except (ValueError, TypeError):
            keep = []
            warn.append("the page list could not be read; kept every page")
        out["pages"] = pages_to_str([p + 1 for p in keep]) if keep else pages_to_str(range(1, n + 1))
        ch = d.get("chapters")
        if ch:
            ch = [c for c in ch if isinstance(c, dict) and c.get("title") and str(c.get("page", "")).isdigit()]
        out["chapters"] = ch or None
    else:
        n = len(dossier["sections"])
        drop = sorted({int(x) for x in (d.get("drop") or []) if str(x).isdigit() and 1 <= int(x) <= n})
        if len(drop) >= n:
            warn.append("the AI proposed dropping every section; ignored the drop list")
            drop = []
        out["drop"] = drop
        out["titles"] = {str(k): str(v) for k, v in (d.get("titles") or {}).items() if str(k).isdigit() and v}
    out["_source"] = d.get("_source", "ai")
    return out, warn


def apply_patch(kind: str, decisions: dict, patch: dict, corpus: str, dossier: dict) -> tuple[dict, list, list]:
    """A review round's corrections, checked: replacements must match text in the book and stay short; page
    changes cannot cut much; a chapter list cannot shrink by half. Returns (new decisions, applied, ignored)."""
    new = copy.deepcopy(decisions)
    applied, ignored = [], []
    flat = " ".join(corpus.split())
    reps = [list(p) for p in (new.get("replacements") or []) if isinstance(p, (list, tuple)) and len(p) == 2]
    for p in patch.get("add_replacements") or []:
        if not (isinstance(p, (list, tuple)) and len(p) == 2 and str(p[0]).strip()):
            ignored.append(f"malformed replacement {p!r}"[:100])
            continue
        find, repl = str(p[0]), str(p[1])
        if len(find) > 120:
            ignored.append(f"replacement too long: {find[:40]}...")
        elif find not in corpus and " ".join(find.split()) not in flat:
            ignored.append(f"text not found in the book: \"{find[:60]}\"")
        elif any(r[0] == find for r in reps):
            ignored.append(f"already replacing \"{find[:60]}\"")
        else:
            reps.append([find, repl])
            applied.append(f"replace \"{find[:60]}\" with \"{repl[:60]}\"")
    for find in patch.get("remove_replacements") or []:
        before = len(reps)
        reps = [r for r in reps if r[0] != find]
        (applied if len(reps) < before else ignored).append(f"remove replacement \"{str(find)[:60]}\"")
    new["replacements"] = reps
    if kind == "pdf":
        if patch.get("pages"):
            n = dossier.get("page_count") or len(dossier["pages"])
            try:
                old_keep, new_keep = parse_pages(new.get("pages"), n), parse_pages(patch["pages"], n)
            except (ValueError, TypeError):
                old_keep, new_keep = [], []
            if new_keep and len(new_keep) >= 0.6 * max(1, len(old_keep)):
                if new_keep != old_keep:
                    new["pages"] = pages_to_str([p + 1 for p in new_keep])
                    added = sorted(set(new_keep) - set(old_keep))
                    removed = sorted(set(old_keep) - set(new_keep))
                    applied.append("pages: " + (f"restored {pages_to_str([p + 1 for p in added])} " if added else "")
                                   + (f"cut {pages_to_str([p + 1 for p in removed])}" if removed else ""))
            else:
                ignored.append("page change would cut too much; ignored")
        if patch.get("chapters"):
            ch = [c for c in patch["chapters"] if isinstance(c, dict) and c.get("title") and str(c.get("page", "")).isdigit()]
            if len(ch) >= max(1, int(0.5 * len(new.get("chapters") or []))):
                if ch != new.get("chapters"):
                    new["chapters"] = ch
                    applied.append(f"chapters list revised ({len(ch)} entries)")
            else:
                ignored.append("chapters list shrank by more than half; ignored")
    else:
        if "drop" in patch and patch["drop"] is not None:
            n = len(dossier["sections"])
            nd = sorted({int(x) for x in patch["drop"] if str(x).isdigit() and 1 <= int(x) <= n})
            if len(nd) >= n:
                ignored.append("drop list would remove every section; ignored")
            elif nd != sorted(int(x) for x in (new.get("drop") or [])):
                new["drop"] = nd
                applied.append(f"drop list now {nd}")
        if patch.get("titles"):
            t = dict(new.get("titles") or {})
            for k, v in patch["titles"].items():
                if v:
                    t[str(k)] = str(v)
                    applied.append(f"title #{k}: \"{v}\"")
                elif str(k) in t:
                    t.pop(str(k))
                    applied.append(f"title #{k} removed")
            new["titles"] = t
    if isinstance(patch.get("flags"), list):
        nf = [f for f in patch["flags"] if f in FLAGS]
        if sorted(nf) != sorted(new.get("flags") or []):
            new["flags"] = nf
            applied.append(f"flags now {nf or 'none'}")
    return new, applied, ignored


def dumps(d: dict) -> str:
    return json.dumps({k: v for k, v in d.items() if not k.startswith("_") and k != "notes"}, ensure_ascii=False)
