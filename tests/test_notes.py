from fiatvox.model import Book, Section
from fiatvox.prepare.notes import notes_start, strip_notes

BODY = ["The battle opened at dawn.", "Each side had learned from the last war."]
NOTES = ["1. Sun Tzu, Art of War, Lionel Giles, trans., 31.",
         "2. Ibid., 29-30.",
         "3. James Chambers, The Devil's Horsemen (New York: Atheneum, 1979), 1-18.",
         "4. Other examples of this phenomenon abound."]


def test_notes_heading_and_entries_are_cut():
    assert notes_start(BODY + ["NOTES"] + NOTES) == 2


def test_numbered_citations_without_a_heading_are_cut():
    assert notes_start(BODY + NOTES) == 2


def test_lone_heading_left_at_the_end_is_cut():
    assert notes_start(BODY + ["Notes"]) == 2


def test_heading_glued_to_the_first_note_in_a_pdf():
    paras = BODY + ["NOTES 1. Historiographical approaches unaware of the existing identity",
                    "and the context of operations, tend to identify the advent of modern warfare with",
                    "Hebrew), pp. 117-20; P. Paret, Understanding War (Princeton, 1992), pp. 209-14;",
                    "History of Military Power (London, 1983), p. 73."]
    assert notes_start(paras) == 2


def test_numbered_lists_in_the_body_are_kept():
    lists = ["Maneuver theory seeks the following:", "1. Preemption: defeating the enemy before the fight",
             "2. Dislocation: rendering the enemy's strength irrelevant", "3. Disruption: defeating the enemy"]
    assert notes_start(lists) is None
    assert notes_start(lists + BODY) is None


def test_strip_notes_reports_each_section():
    book = Book(title="T", author="A", sections=[Section(title="Chapter One", paragraphs=BODY + ["NOTES"] + NOTES),
                                                 Section(title="Chapter Two", paragraphs=list(BODY))])
    cut = strip_notes(book)
    assert book.sections[0].paragraphs == BODY and book.sections[1].paragraphs == BODY
    assert cut == ["Chapter One: 5 paragraph(s) of endnotes"]
