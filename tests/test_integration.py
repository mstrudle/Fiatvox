"""End to end: a tiny generated EPUB narrated by Kokoro into an .m4b with the right chapters and durations."""
import zipfile

import pytest

from fiatvox.narrate.kokoro_engine import SAMPLE_RATE

CHAPTERS = [
    ("Chapter One", "The Beginning", ["The U.S. Army moved north in World War II.", "Lt. Col. Smith led them."]),
    ("Chapter Two", "Napoleon III", ["Napoleon III was an emperor.", "He weighed 180 lb."]),
    ("Chapter Three", "The End", ["XVIII Airborne Corps landed.", "The lieutenant was a quasi hero."]),
]


def make_epub(path):
    items, spine, nav = [], [], []
    for i, (label, title, paras) in enumerate(CHAPTERS, 1):
        body = f"<p>{label}</p><h1>{title}</h1>" + "".join(f"<p>{p}</p>" for p in paras)
        items.append((f"c{i}.xhtml", f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                      f"<head><title>{title}</title></head><body>{body}</body></html>"))
        spine.append(f'<itemref idref="c{i}"/>')
        nav.append(f'<li><a href="c{i}.xhtml">{title}</a></li>')
    nav_doc = ('<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
               'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
               f'<nav epub:type="toc"><ol>{"".join(nav)}</ol></nav></body></html>')
    manifest = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
                       for i in range(1, len(CHAPTERS) + 1))
    opf = ('<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="id">test</dc:identifier><dc:title>Tiny Test Book</dc:title>'
           '<dc:creator>Test Author</dc:creator><dc:language>en</dc:language></metadata>'
           f'<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>{manifest}'
           f'</manifest><spine>{"".join(spine)}</spine></package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav_doc)
        for name, doc in items:
            z.writestr(f"OEBPS/{name}", doc)


@pytest.mark.slow
def test_tiny_epub_to_m4b(tmp_path, monkeypatch):
    monkeypatch.setenv("FIATVOX_DATA", str(tmp_path / "data"))
    from fiatvox.cli import main
    from fiatvox.model import Book
    from fiatvox.narrate import assemble, cache
    from fiatvox.narrate.narrator import plan
    from fiatvox.settings import Settings

    src = tmp_path / "tiny.epub"
    make_epub(src)
    out = tmp_path / "out"
    rc = main(["make", str(src), "--voice", "bm_george", "--out", str(out), "--no-ai", "--no-import"])
    assert rc == 0
    m4b = out / "Test Author - Tiny Test Book.m4b"
    assert m4b.exists() and (out / "Test Author - Tiny Test Book - QC report.md").exists()

    chapters = assemble.probe_chapters(m4b)
    assert [c["title"] for c in chapters] == ["Chapter One: The Beginning", "Chapter Two: Napoleon III",
                                              "Chapter Three: The End"]

    # expected durations from the cached clips plus the configured silences, to within 50 ms
    from fiatvox import paths
    book = Book.load(next(paths.books_dir().iterdir()) / "book.json")
    st = Settings.load()
    p = plan(book, "bm_george", st.silences())
    import soundfile as sf
    for ch, ks, got in zip(p.chapters, p.keys, chapters):
        samples = sum(int(round(u.pause_before * SAMPLE_RATE)) + sf.info(str(cache.path_for(k))).frames
                      + int(round(u.pause_after * SAMPLE_RATE)) for u, k in zip(ch.units, ks))
        assert abs((got["end"] - got["start"]) - samples / SAMPLE_RATE) < 0.05

    # the spoken copy: stray "Chapter One" label gone, numerals and abbreviations written out, override whole
    spoken = [u.text for ch in p.chapters for u in ch.units]
    assert spoken[0] == "Chapter One: The Beginning."
    assert "The US Army moved north in World War Two." in spoken
    assert "[Lieutenant](/luːtˈɛnənt/) Colonel Smith led them." in spoken  # British override after expansion
    assert "Napoleon the Third was an emperor." in spoken and "He weighed 180 pounds." in spoken
    assert "Eighteenth Airborne Corps landed." in spoken
    assert "The [lieutenant](/luːtˈɛnənt/) was a [quasi](/kwˈɑːzi/) hero." in spoken
    assert not any(t in ("Chapter One", "Chapter One.") for t in spoken[1:])

    # a second run renders nothing new: everything comes from the cache
    rc = main(["make", str(src), "--voice", "bm_george", "--out", str(out), "--no-ai", "--no-import"])
    assert rc == 0 and p.chars(only_missing=True) == 0
