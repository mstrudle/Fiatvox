"""A lost drop cap is detected from the indented, lowercase first line; letters are only kept if they make a word."""
from fiatvox.ingest import ocr


def line(x, y, text, h=10):
    out, cx = [], x
    for t in text.split():
        out.append((cx, y, cx + 6 * len(t), y + h, t))
        cx += 6 * len(t) + 4
    return out


def test_candidate_when_first_line_is_indented_and_lowercase():
    words = line(100, 20, "CHAPTER II", h=12)
    words += line(140, 100, "he most natural starting-point")   # three lines indented for the capital
    words += line(142, 112, "Great War in European history")
    words += line(141, 124, "War. We cannot expect much")
    words += line(100, 136, "when strategy was in its infancy")
    words += line(100, 148, "is too deeply stamped on the mind")
    c = ocr.drop_cap_candidates(words)
    assert [words[i][4] for i, _ in c] == ["he"]
    x0, y0, x1, y1 = c[0][1]
    assert x0 < 100 and x1 < 140 and y0 < 100 and y1 > 124


def test_no_candidate_for_ordinary_paragraphs():
    words = line(100, 100, "the line above runs to the margin")
    words += line(130, 112, "and this one is indented a little")
    words += line(100, 124, "then back at the margin again")
    words += line(100, 136, "and once more")
    assert ocr.drop_cap_candidates(words) == []


def test_letter_must_make_a_word():
    assert ocr._is_word("The") and ocr._is_word("When,") and ocr._is_word("After")
    assert not ocr._is_word("Ahe") and not ocr._is_word("Cstrategy,") and not ocr._is_word("Dзед.")


def test_merged_capital_in_next_line():
    first = (457, 147, 474, 164, "he")
    tall = (426, 151, 488, 186, "Tray")
    assert ocr.merged_cap([first, tall], 0) == 1
    assert ocr.merged_cap([first, (426, 151, 488, 162, "Tray")], 0) is None   # an ordinary-height word
