"""The QC report: a markdown file next to the audiobook. Ported from audiobook.write_report, plus narration facts."""
from __future__ import annotations

import datetime
import html
from pathlib import Path


def _hms(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def status_of(qc: dict) -> tuple[str, list[str]]:
    a1 = qc["audit_after"]
    issues = []
    if qc.get("validation"):
        issues.append(f"{len(qc['validation'])} structural problem(s)")
    if a1["verdict"] in ("NOTICEABLE", "HEAVY"):
        issues.append(f"text damage is still {a1['verdict'].lower()} ({a1['rate']} per 1,000 words)")
    if qc.get("tiny"):
        issues.append(f"{qc['tiny']} very short section(s) to check")
    if qc.get("unmatched"):
        issues.append("some chapter headings were placed by page, not matched")
    if qc.get("debris"):
        issues.append(f"{len(qc['debris'])} possible leftover page-furniture fragment(s) in the text")
    return ("GOOD" if not issues else "CHECK THESE POINTS"), issues


def write(path: Path, qc: dict, narration: dict | None) -> Path:
    d = qc["decisions"]
    a0, a1 = qc["audit_before"], qc["audit_after"]
    words = sum(s["words"] for s in qc["sections_after"])
    status, issues = status_of(qc)
    L = [f"# QC report: {qc.get('title') or d.get('title') or Path(qc['source']).stem}", "", "## Bottom line", ""]
    L.append(f"1. **Status: {status}.**" + ("" if not issues else " " + "; ".join(issues) + "."))
    if narration:
        L.append(f"2. **Audiobook:** `{Path(narration['file']).name}`: {len(narration['chapters'])} chapters, "
                 f"{_hms(narration['duration'])}, voice {narration['voice']}.")
    else:
        L.append(f"2. **Prepared:** {len(qc['sections_after'])} sections, {words:,} words (not narrated yet).")
    L.append(f"3. **Text quality:** {a0['rate']} suspected problems per 1,000 words before AI correction, "
             f"{a1['rate']} after ({a1['verdict'].lower()}).")
    src = "the AI" if d.get("_source") == "ai" else "local heuristics"
    L.append(f"4. **Decisions by:** {src} ({qc.get('ai', '')}).")
    rv = qc.get("review") or []
    acc = sum(1 for h in rv if h.get("outcome") == "accepted")
    L.append(f"5. **Automated review:** {len(rv)} round(s), {acc} accepted; stopped because {qc.get('review_stop') or 'n/a'}.")
    u = qc.get("usage") or {}
    if u.get("calls"):
        cost = ""
        if u.get("cost"):
            cost = (f", about ${u['cost']:.2f} at API prices, covered by your plan" if u.get("billing") == "sub"
                    else f", about ${u['cost']:.2f}")
        cached = f" ({u['cache_read']:,} more read from the cache)" if u.get("cache_read") else ""
        L.append(f"6. **AI use:** {u['calls']} request(s), {u['input_tokens']:,} tokens in{cached}, "
                 f"{u['output_tokens']:,} out{cost}.")
    L.append("")
    if qc.get("note"):
        L += ["## Listener's note", "", f"> {qc['note']}", ""]
    if qc.get("warnings"):
        L += ["## Warnings", ""] + [f"- {w}" for w in qc["warnings"]] + [""]
    if rv:
        L += ["## Automated review rounds", ""]
        for h in rv:
            sc = f" (problem score {h['score'][0]} to {h['score'][1]})" if h.get("score") else ""
            L += [f"**Round {h['round']}: {h['outcome']}{sc}**", ""]
            L += [f"- AI: {x}" for x in h.get("reasons") or []]
            verb = "Tried and undone" if str(h.get("outcome", "")).startswith("reverted") else "Applied"
            L += [f"- {verb}: {x}" for x in h.get("applied") or []]
            L += [f"- Ignored: {x}" for x in h.get("ignored") or []]
            L.append("")

    L += ["## Chapters", ""]
    if narration:
        L += ["| # | Chapter | Starts | Length | Words | Opens with |", "|---|---|---|---|---|---|"]
        secs = [qc["sections_after"][i] for i in narration.get("sections", range(len(narration["chapters"])))]
        for s, (t, a, b) in zip(secs, narration["chapters"]):
            op = s["opening"][:80].replace("|", "/")
            L.append(f"| {s['n']} | {t[:80].replace('|', '/')} | {_hms(a)} | {_hms(b - a)} | {s['words']:,} | {op} |")
    else:
        L += ["| # | Chapter | Words | Opens with |", "|---|---|---|---|"]
        for s in qc["sections_after"]:
            L.append(f"| {s['n']} | {s['title'][:80].replace('|', '/')} | {s['words']:,} | {s['opening'][:80].replace('|', '/')} |")
    L.append("")

    L += ["## What was cut and why", ""]
    if qc["type"] == "pdf":
        L.append(f"- **Pages kept:** {qc.get('pages_kept')}")
        L.append(f"- **Pages cut:** {qc.get('pages_cut') or 'none'}")
    else:
        before = {n: (t, w) for n, t, w in qc.get("sections_before", [])}
        if qc.get("dropped"):
            L.append("- **Sections cut:** " + "; ".join(
                f"#{n} {before.get(n, ('?', 0))[0][:40]} ({before.get(n, ('?', 0))[1]} words)" for n in qc["dropped"]))
        else:
            L.append("- **Sections cut:** none")
    L += [f"- {n}" for n in d.get("notes") or []]
    L.append("")

    if qc["type"] == "pdf" and qc.get("build"):
        b = qc["build"]
        rm = b.get("removed", {})
        L += ["## Page furniture removed", "",
              f"- Running headers: {sum(v for k, v in rm.items() if k.startswith('header'))} lines; "
              f"footers: {sum(v for k, v in rm.items() if k.startswith('footer'))}; page numbers: {rm.get('page number', 0)}; "
              f"footnote lines: {rm.get('footnote line', 0)}; note markers: {b.get('note_markers', 0)}"]
        if b.get("two_column_pages"):
            L.append(f"- Pages read as two columns: {b['two_column_pages']}")
        if b.get("chapter_matches"):
            L += ["", "| Page | Chapter title | Printed heading matched | Found |", "|---|---|---|---|"]
            for m in b["chapter_matches"]:
                L.append(f"| {m['page']} | {m['title'][:60]} | "
                         f"{html.escape(str(m.get('matched_line', ''))[:50]).replace('|', '/')} | "
                         f"{'yes' if m.get('found') else '**no: placed at page top**'} |")
        L.append("")

    L += ["## Cleanup applied", "",
          f"- **Flags:** {', '.join(d.get('flags') or []) or 'none'} (plus the standard note-marker, URL and hyphenation cleanup)"]
    reps = d.get("replacements") or []
    L.append(f"- **Book-specific replacements:** {len(reps)}"
             + (": " + "; ".join(f"`{a}` to `{b}`" for a, b in reps[:25]) if reps else ""))
    fx = qc.get("ocr_fix")
    if fx:
        L.append(f"- **AI OCR correction:** {fx['accepted']} paragraphs corrected, {fx['rejected']} rejected by the "
                 "safety check; every change is in `ai-log.md` in the book's work folder")
    L += [f"- {e}" for e in qc.get("events") or []]
    L.append("")

    ch = qc.get("chapters") or {}
    L += ["## Prepared for the narrator", ""]
    if ch.get("removed_labels"):
        L.append(f"- **Stray labels removed next to headings:** {len(ch['removed_labels'])}, for example "
                 + "; ".join(f'"{t[:40]}"' for _, t in ch["removed_labels"][:6]))
    if ch.get("renumbered"):
        L.append(f"- **Chapters renumbered into one sequence:** {len(ch['renumbered'])}")
    if narration:
        fx = narration.get("spoken_fixes") or []
        L.append(f"- **Numerals, units and abbreviations written out:** {sum(n for _, n in fx)} change(s)"
                 + (". Check these read correctly:" if fx else ""))
        L += [f"  - {n} x `{k}`" for k, n in fx[:60]]
        sp = narration.get("spelling_fixes") or []
        if sp:
            L.append(f"- **Spelling substitutions:** " + "; ".join(f"{n} x `{k}`" for k, n in sp[:20]))
        px = narration.get("phoneme_fixes") or []
        L.append(f"- **Exact pronunciations:** {sum(n for _, n in px)} word(s)"
                 + (": " + "; ".join(f"{n} x `{k}`" for k, n in px[:20]) if px else ""))
    L.append("")

    L += ["## Remaining flags (sample)", "",
          f"{a1['issues']} suspected problems remain in {a1['words']:,} words. Most are real but rare words, names "
          "and foreign phrases.", ""]
    for k, v in a1["examples"].items():
        L.append(f"- **{k}:** {' / '.join(v[:10])}")
    if qc.get("debris"):
        L += ["", "Possible leftover page furniture (read aloud if real):", ""]
        L += [f"- #{h['n']}: \"{h['text']}\" in \"...{h['context'][-110:]}\"" for h in qc["debris"][:15]]
    L.append("")
    if qc.get("validation"):
        L += ["## Structural problems", ""] + [f"- {p}" for p in qc["validation"]] + [""]
    L += ["## Listener's checklist", "",
          "- [ ] The chapter list matches the book (nothing important cut, no junk kept)",
          "- [ ] Chapter titles are spoken correctly",
          "- [ ] Numerals, abbreviations and listed pronunciations sound right",
          "", f"_Generated by Fiat Vox {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} from `{qc['source']}`._"]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path
