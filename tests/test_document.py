"""RTF, DOCX and text files are read through macOS's textutil and split into sections at their headings."""
import subprocess

import pytest

from fiatvox.ingest.document import is_heading, read_document

HTML = """<html><head><title>On War</title><meta name="author" content="Carl von Clausewitz"></head><body>
<p>Translated by J. J. Graham.</p>
<h1>BOOK I</h1><h2>ON THE NATURE OF WAR</h2>
<h3>CHAPTER I.</h3><p><b>What is War?</b></p>
<p>We propose to consider first the single elements of our subject, then each branch or part.</p>
<p>War is nothing but a duel on an extensive scale.</p>
<h3>CHAPTER II.</h3><p><b>End and Means in War</b></p>
<p>Having in the foregoing chapter ascertained the complicated and variable nature of War, we shall now occupy
ourselves in examining into the influence which this nature has upon the end and means in War.</p>
</body></html>"""


@pytest.mark.parametrize("fmt", ["rtf", "docx", "txt"])
def test_formats_split_at_headings(tmp_path, fmt):
    src_html = tmp_path / "onwar.html"
    src_html.write_text(HTML)
    src = tmp_path / f"onwar.{fmt}"
    subprocess.run(["textutil", "-convert", fmt, "-output", str(src), str(src_html)], check=True)
    book = read_document(src, tmp_path)
    labels = [s.label for s in book.sections]
    assert any("CHAPTER I" in l for l in labels) and any("CHAPTER II" in l for l in labels)
    ch2 = next(s for s in book.sections if "CHAPTER II" in s.label)
    assert ch2.paragraphs and ch2.paragraphs[-1].startswith("Having in the foregoing chapter")
    assert sum(s.words for s in book.sections) > 50


@pytest.mark.parametrize("text,heading", [
    ("CHAPTER III.", True), ("BOOK IV", True), ("ON THE NATURE OF WAR", True),
    ("War is nothing but a duel on an extensive scale.", False),
    ("He said NO.", False),
])
def test_heading_rules_without_styles(text, heading):
    assert is_heading(text, 12.0, False, 12.0) is heading
