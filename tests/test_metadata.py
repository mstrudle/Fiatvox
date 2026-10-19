"""FFMETADATA chapters and tags, file names, and the per-book work folder id."""
from pathlib import Path

from fiatvox.cli import book_id
from fiatvox.narrate.assemble import ffmetadata, safe_filename


def test_ffmetadata_chapters_in_samples():
    m = ffmetadata("Strategy", "B. H. Liddell Hart", [("Preface", 0, 48000), ("Chapter One: History", 48000, 96000)])
    lines = m.splitlines()
    assert lines[0] == ";FFMETADATA1"
    assert "title=Strategy" in lines and "artist=B. H. Liddell Hart" in lines and "genre=Audiobook" in lines
    assert m.count("[CHAPTER]") == 2
    assert "TIMEBASE=1/24000" in lines and "START=48000" in lines and "END=96000" in lines
    assert "title=Chapter One: History" in lines


def test_ffmetadata_escapes_special_characters():
    m = ffmetadata("A=B; C#D", "X\\Y", [("Part One; = #", 0, 1)])
    assert "title=A\\=B\\; C\\#D" in m
    assert "artist=X\\\\Y" in m
    assert "title=Part One\\; \\= \\#" in m


def test_safe_filename():
    assert safe_filename('P. W. Singer - Wired for War: Robots/"AI"') == "P. W. Singer - Wired for War Robots AI"
    assert safe_filename("...") == "Audiobook"


def test_book_id_differs_by_folder(tmp_path):
    a = tmp_path / "one" / "book.epub"
    b = tmp_path / "two" / "book.epub"
    for p in (a, b):
        p.parent.mkdir()
        p.write_text("x")
    assert book_id(a) != book_id(b)
    assert book_id(a).startswith("book-") and book_id(a) == book_id(Path(str(a)))
