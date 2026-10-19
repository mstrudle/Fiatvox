"""The AI only returns decisions; Python validates them. These are the safety checks, with a mock backend."""
import json

import pytest

from fiatvox.ai import AIError, ask_json
from fiatvox.ai.base import Usage
from fiatvox.ai.pi_backend import parse_jsonl
from fiatvox.model import Book, Section
from fiatvox.prepare import decisions as dec
from fiatvox.prepare import ocrfix


class Mock:
    name, label = "mock", "mock"

    def __init__(self, replies):
        self.replies = list(replies)
        self.usage = Usage()
        self.prompts = []

    def available(self):
        return True, ""

    def complete(self, prompt, *, purpose, timeout=900):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_ask_json_strips_fences_and_retries_once():
    assert ask_json(Mock(['```json\n{"a": 1}\n```']), "p", purpose="t") == {"a": 1}
    m = Mock(["not json", '{"b": 2}'])
    assert ask_json(m, "p", purpose="t") == {"b": 2}
    assert "not valid JSON" in m.prompts[1]
    with pytest.raises(AIError):
        ask_json(Mock(["nope", "still nope"]), "p", purpose="t")


EPUB_DOSSIER = {"sections": [{"n": i} for i in range(1, 6)], "audit": {}}
PDF_DOSSIER = {"page_count": 100, "pages": [{"page": i} for i in range(1, 101)]}


def test_sanitise_epub_rejects_dropping_everything():
    d, warn = dec.sanitise("epub", {"drop": [1, 2, 3, 4, 5], "flags": ["fix_ligatures", "rm_rf"]}, EPUB_DOSSIER)
    assert d["drop"] == [] and any("every section" in w for w in warn)
    assert d["flags"] == ["fix_ligatures"]


def test_sanitise_drops_malformed_and_long_replacements():
    d, warn = dec.sanitise("epub", {"replacements": [["ok", "fine"], ["x" * 200, ""], "bad", ["", "y"]]},
                           EPUB_DOSSIER)
    assert d["replacements"] == [["ok", "fine"]] and len(warn) == 3


def test_sanitise_pdf_pages_and_chapters():
    d, _ = dec.sanitise("pdf", {"pages": "5-20,30", "chapters": [{"page": 5, "title": "Preface"},
                                                                 {"page": "x", "title": "Bad"}, {"page": 9}]},
                        PDF_DOSSIER)
    assert d["pages"] == "5-20,30" and d["chapters"] == [{"page": 5, "title": "Preface"}]


def test_patch_replacement_must_exist_in_book():
    base = {"replacements": [], "drop": [], "titles": {}, "flags": []}
    new, applied, ignored = dec.apply_patch("epub", base, {"add_replacements": [["RectrAL", "Recital"],
                                                                                ["Nowhere", "x"]]},
                                            "The RectrAL began.", EPUB_DOSSIER)
    assert new["replacements"] == [["RectrAL", "Recital"]]
    assert len(applied) == 1 and "not found" in ignored[0]


def test_patch_page_cut_limited():
    base = {"pages": "1-100", "chapters": [{"page": 1, "title": "A"}] * 10, "replacements": [], "flags": []}
    new, applied, ignored = dec.apply_patch("pdf", base, {"pages": "1-50"}, "", PDF_DOSSIER)
    assert new["pages"] == "1-100" and "cut too much" in ignored[0]
    new, applied, _ = dec.apply_patch("pdf", base, {"pages": "1-90"}, "", PDF_DOSSIER)
    assert new["pages"] == "1-90" and applied


def test_patch_chapter_list_cannot_halve():
    base = {"pages": "1-100", "chapters": [{"page": i, "title": f"C{i}"} for i in range(1, 11)],
            "replacements": [], "flags": []}
    new, _, ignored = dec.apply_patch("pdf", base, {"chapters": [{"page": 1, "title": "Only"}]}, "", PDF_DOSSIER)
    assert len(new["chapters"]) == 10 and "shrank" in ignored[0]


@pytest.mark.parametrize("old,new,ok", [
    ("Tbe army rnoved north.", "The army moved north.", True),
    ("The army moved north.", "The troops went northwards in haste.", False),   # too different
    ("The army moved north.", "", False),
    ("A long sentence about the war and the army.", "A long sentence.", False),  # length
])
def test_ocr_guard(old, new, ok):
    assert ocrfix.acceptable(old, new)[0] is ok


def test_ocr_fix_applies_only_guarded_changes(tmp_path):
    book = Book(sections=[Section(title="One", paragraphs=["Tbe army rnoved north quickly.",
                                                           "The scouts went ahead of the colunm."])])
    reply = json.dumps({"0.0": "The army moved north quickly.", "0.1": "Completely rewritten text here, sir."})
    out, info = ocrfix.correct(book, Mock([reply]), tmp_path)
    assert out.sections[0].paragraphs[0] == "The army moved north quickly."
    assert out.sections[0].paragraphs[1] == "The scouts went ahead of the colunm."
    assert info["accepted"] == 1 and info["rejected"] == 1
    log = (tmp_path / "ai-log.md").read_text()
    assert "accepted" in log and "REJECTED" in log
    # cached: a second run sends nothing
    out2, info2 = ocrfix.correct(book, Mock([]), tmp_path)
    assert info2["sent"] == 0 and out2.sections[0].paragraphs[0] == "The army moved north quickly."


def test_pi_jsonl_final_message():
    lines = [{"type": "session", "version": 3},
             {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "{\"a\""}},
             {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "{\"a\": 1}"}],
                                                 "usage": {"input": 120, "output": 8, "cost": {"total": 0.001}}}},
             {"type": "agent_end"}]
    text, usage, err = parse_jsonl("\n".join(json.dumps(x) for x in lines) + "\n")
    assert text == '{"a": 1}' and usage == {"input": 120, "output": 8, "cost": 0.001} and not err
