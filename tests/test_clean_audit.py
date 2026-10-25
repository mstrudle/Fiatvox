"""Text cleanup, the damage audit, and EPUB markup cleanup."""
import pytest

from fiatvox.ingest.epub import clean_markup, paragraphs_of, split_heading
from fiatvox.model import Book, Section
from fiatvox.prepare.audit import audit, paragraph_issues
from fiatvox.prepare.clean import clean_book


@pytest.mark.parametrize("flags,before,after", [
    ([], "move- ments of the army", "movements of the army"),
    ([], "see www.example.com/page for more", "see for more"),
    ([], "an area of 5 km² here", "an area of 5 km squared here"),
    ([], "e.g. tanks, i.e. armour", "for example tanks, that is armour"),
    ([], "as Smith et al. wrote", "as Smith and colleagues wrote"),
    (["fix_ligatures"], "a fi ction about offi cers", "a fiction about officers"),
    ([], "a fi ction", "a fi ction"),
    (["strip_glued_numbers"], "the war.23 The end", "the war. The end"),
    (["strip_citations"], "as shown (Smith 2019, 45) before", "as shown before"),
])
def test_clean_rules(flags, before, after):
    b = clean_book(Book(sections=[Section(title="T", paragraphs=[before])]), flags)
    assert b.sections[0].paragraphs == [after]


def test_book_replacements_whole_word_and_titles():
    b = Book(sections=[Section(title="Chapter One: Simonps", paragraphs=["Simonps and Simonpsy"])])
    out = clean_book(b, [], [["Simonps", "Simonds"]])
    assert out.sections[0].paragraphs == ["Simonds and Simonpsy"]
    assert out.sections[0].title == "Chapter One: Simonds"
    assert b.sections[0].paragraphs == ["Simonps and Simonpsy"]  # the original is untouched


def test_audit_counts_damage():
    words = set("the army moved north and reached river by night".split())
    issues = paragraph_issues("the arrny rnoved n0rth fi ction", __import__("collections").Counter(), words)
    reasons = {r for _, r in issues}
    assert "split ligature" in reasons and "letters and digits mixed" in reasons
    a = audit(Book(sections=[Section(title="T", paragraphs=["The army moved north. " * 50])]))
    assert a["verdict"] == "CLEAN" and a["issues"] == 0


def test_epub_markup_cleanup():
    html = ('<p>War<sup><a href="#n1">12</a></sup> began<a epub:type="noteref" href="#n2">2</a>.</p>'
            '<p><span epub:type="pagebreak" title="44">44</span>Next</p><p>17</p>')
    assert paragraphs_of(clean_markup(html)) == ["War began.", "Next"]


def test_source_line_breaks_are_not_paragraphs():
    assert paragraphs_of("<p>This line\n  wraps in the source</p><p>Second</p>") == ["This line wraps in the source",
                                                                                   "Second"]


def test_top_heading_is_the_title():
    before, heading, after = split_heading("<p>Chapter 3</p><h1>The Retreat</h1><p>Text</p>", "")
    assert heading == "The Retreat" and paragraphs_of(before) == ["Chapter 3"]
    # an h2 only counts when it matches the table-of-contents label
    assert split_heading("<h2>Aside</h2><p>x</p>", "Chapter Two")[1] == ""
    assert split_heading("<h2>Chapter Two</h2><p>x</p>", "Chapter Two")[1] == "Chapter Two"


def test_tables_and_equations_become_short_notes():
    html = ("<p>Before.</p><table><tr><td>12.4</td><td>13.1</td></tr></table>"
            "<p>Mid.</p><m:math><m:mi>x</m:mi></m:math><p>After.</p>")
    assert paragraphs_of(clean_markup(html)) == ["Before.", "Table omitted.", "Mid.", "Equation omitted.", "After."]


def test_layout_table_with_prose_is_kept():
    prose = "This is a long paragraph of real prose that a publisher placed inside a table cell for layout reasons."
    assert paragraphs_of(clean_markup(f"<table><tr><td><p>{prose}</p></td></tr></table>")) == [prose]
