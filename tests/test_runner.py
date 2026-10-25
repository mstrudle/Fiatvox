"""The queue and the background worker: a review stop pauses after one chapter; continuing finishes the book."""
import json
from pathlib import Path

import pytest

from tests.test_integration import make_epub


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIATVOX_DATA", str(tmp_path / "data"))
    from fiatvox.settings import Settings
    s = Settings(ai_provider="none", import_to_books=False, notify=False, output_dir=str(tmp_path / "out"))
    s.save()
    return tmp_path


def test_queue_survives_restart_and_resumes_interrupted(env):
    from fiatvox import bookqueue as q
    qu = q.Queue()
    a = qu.add(str(env / "a.epub"), "bm_george", str(env / "out"))
    b = qu.add(str(env / "b.epub"), "bm_george", str(env / "out"), review_stop=True)
    a.status = q.NARRATING
    qu.save()
    again = q.Queue.load()
    assert [i.id for i in again.items] == [a.id, b.id]
    assert again.items[0].status == q.WAITING and "resume" in again.items[0].message
    again.move(b.id, -1)
    assert [i.id for i in q.Queue.load().items] == [b.id, a.id]


@pytest.mark.slow
def test_review_stop_then_continue(env):
    from fiatvox import bookqueue as q
    from fiatvox.runner import Runner
    src = env / "tiny.epub"
    make_epub(src)
    qu = q.Queue()
    item = qu.add(str(src), "bm_george", str(env / "out"), review_stop=True)
    events = []
    r = Runner(qu, lambda it, ev: events.append(ev.get("type")))
    r._loop()
    assert item.status == q.REVIEW, (item.status, item.error, events)
    assert Path(item.preview).exists() and item.review_chapter.startswith("Chapter One")
    assert "prepared" in events and "review" in events
    item.reviewed, item.status = True, q.WAITING
    r._loop()
    assert item.status == q.DONE, (item.error, events[-5:])
    assert Path(item.file).exists() and item.progress == 1.0
    saved = json.loads(q.Queue.path().read_text())
    assert saved["items"][0]["status"] == "done"
