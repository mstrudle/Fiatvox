"""Spoken units: one per title and paragraph, silences in the right places, overrides kept whole."""
from fiatvox.model import Book, Section
from fiatvox.narrate import chunk


def speaker(accent="b", lexicon=None, spelling=()):
    return chunk.Speaker(accent, lexicon or {}, list(spelling))


def test_units_and_silences():
    book = Book(sections=[Section(title="Chapter One: The U.S. Army", paragraphs=["First para.", "Second\npara."])])
    sil = chunk.Silences(before_title=1.5, after_title=0.8, paragraph=0.4, chapter_end=1.0)
    chs = chunk.chapters(book, speaker(), sil)
    assert len(chs) == 1 and chs[0].title == "Chapter One: The U.S. Army"  # chapter name stays as written
    u = chs[0].units
    assert [x.kind for x in u] == ["title", "para", "para"]
    assert u[0].text == "Chapter One: The US Army."                       # spoken copy is normalised
    assert (u[0].pause_before, u[0].pause_after) == (1.5, 0.8)
    assert (u[1].pause_before, u[1].pause_after) == (0.0, 0.4)
    assert u[2].pause_after == 1.0 and u[2].text == "Second para."        # newline collapsed


def test_never_split_at_abbreviation_periods():
    sp = speaker()
    t = sp.speak("Lt. Col. Smith of the U.S. Army met Gen. Jones.")
    assert t == "Lieutenant Colonel Smith of the US Army met General Jones."
    assert ". " not in t


def test_override_inserted_whole_after_normalisation():
    sp = speaker("b", {"lieutenant": "luːtˈɛnənt"})
    t = sp.speak("Lt. Col. Smith, a lieutenant\nof the line.")
    assert t == "[Lieutenant](/luːtˈɛnənt/) Colonel Smith, a [lieutenant](/luːtˈɛnənt/) of the line."
    assert chunk.check_unit(t) == []


def test_spelling_list_applies_before_overrides():
    sp = speaker("a", {"quasi": "kwˈɑzi"}, [("HIMARS", "High-mars")])
    assert sp.speak("The HIMARS fired, quasi-randomly.") == "The High-mars fired, [quasi](/kwˈɑzi/)-randomly."


def test_check_unit_finds_broken_markup():
    assert chunk.check_unit("a [word](/ph ones/) b")
    assert chunk.check_unit("line\nbreak")
    assert not chunk.check_unit("plain text with (brackets) and a/slash")


def test_spoken_title_gets_a_full_stop():
    assert chunk.spoken_title("Preface") == "Preface."
    assert chunk.spoken_title("Why War?") == "Why War?"


def test_regnal_names_from_the_book():
    book = Book(sections=[Section(title="One", paragraphs=["Archbishop Makarios III spoke.", "Then Makarios III left."])])
    sp = chunk.Speaker.for_voice("bm_george", book)
    assert sp.speak("Then Makarios III left.") == "Then Makarios the Third left."
