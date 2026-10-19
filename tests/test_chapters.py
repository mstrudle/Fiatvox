"""Chapter naming: uniform names, one sequence, stray labels removed, names never normalised."""
import pytest

from fiatvox.model import Book, Section
from fiatvox.prepare.chapters import prepare_chapters, uniform_title


@pytest.mark.parametrize("before,after", [
    ("Chapter Two. The History of Eggs", "Chapter Two: The History of Eggs"),
    ("Part One. Modern Battle", "Part One: Modern Battle"),
    ("Appendix Two. Documents", "Appendix Two: Documents"),
    ("Introduction. Why Robots", "Introduction: Why Robots"),
    ("Chapter Two: Already Fine", "Chapter Two: Already Fine"),
    ("Front matter", "Title Page"),
    ("  Preface  ", "Preface"),
    ("Dr. Strangelove", "Dr. Strangelove"),
])
def test_uniform_title(before, after):
    assert uniform_title(before) == after


def book(*secs):
    return Book(title="T", sections=list(secs))


def test_stray_label_above_heading_is_removed():
    # a "Chapter One" printed above what is really chapter seven
    s = Section(title="Chapter Seven: The Siege", heading="Chapter Seven: The Siege", lead=1,
                paragraphs=["Chapter One", "The siege began at dawn."])
    prepare_chapters(book(*[Section(title=f"Chapter {w}: X", paragraphs=["text."]) for w in
                            ["One", "Two", "Three", "Four", "Five", "Six"]], s))
    assert s.paragraphs == ["The siege began at dawn."]


def test_duplicated_subtitle_after_heading_is_removed():
    s = Section(title="Chapter One: Scenes from a Robot War", heading="",
                paragraphs=["[ONE]", "INTRODUCTION: SCENES FROM A ROBOT WAR", "Scenes from a Robot War",
                            "It was a hot day in Baghdad."])
    prepare_chapters(book(s))
    assert s.paragraphs == ["It was a hot day in Baghdad."]


def test_real_first_paragraph_is_kept():
    s = Section(title="Chapter One: The War", paragraphs=["The war began.", "More text."])
    prepare_chapters(book(s))
    assert s.paragraphs == ["The war began.", "More text."]


def test_book_label_number_kept_in_name():
    s = Section(title="The Long Retreat", heading="The Long Retreat", lead=1,
                paragraphs=["CHAPTER 3", "They fell back."])
    prepare_chapters(book(s))
    assert s.title == "Chapter One: The Long Retreat"  # renumbered: it is the first chapter here
    assert s.paragraphs == ["They fell back."]


def test_one_sequence_through_parts():
    secs = [Section(title="Part One: Early"), Section(title="Chapter One: A", paragraphs=["a."]),
            Section(title="Chapter Two: B", paragraphs=["b."]), Section(title="Part Two: Late"),
            Section(title="Chapter One: C", paragraphs=["c."]), Section(title="Chapter 2. D", paragraphs=["d."])]
    info = prepare_chapters(book(*secs))
    assert info["titles"] == ["Part One: Early", "Chapter One: A", "Chapter Two: B", "Part Two: Late",
                              "Chapter Three: C", "Chapter Four: D"]


def test_titles_not_normalised():
    s = Section(title="Chapter One: Napoleon III and the U.S.", paragraphs=["x."])
    prepare_chapters(book(s))
    assert s.title == "Chapter One: Napoleon III and the U.S."


def test_flat_list_one_marker_per_section():
    secs = [Section(title=f"Chapter {n}: T", paragraphs=["x."]) for n in range(1, 29)]
    info = prepare_chapters(book(*secs))
    assert info["sections"] == 28 and info["titles"][27] == "Chapter Twenty-Eight: T"
