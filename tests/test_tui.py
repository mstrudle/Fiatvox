"""The app, driven headlessly: queue display, adding a book by path, settings, voices, and the review stop."""
import pytest

from tests.test_integration import make_epub


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIATVOX_DATA", str(tmp_path / "data"))
    from fiatvox.settings import Settings
    Settings(ai_provider="none", setup_done=True, import_to_books=False, notify=False,
             output_dir=str(tmp_path / "out")).save()
    from fiatvox import runner
    started = []
    monkeypatch.setattr(runner.Runner, "start", lambda self: started.append(True))
    return tmp_path, started


async def test_add_book_by_path(env):
    tmp, started = env
    from fiatvox import bookqueue as q
    from fiatvox.tui.app import FiatVox
    src = tmp / "My Book.epub"
    make_epub(src)
    app = FiatVox()
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#empty").display
        await pilot.press("a")
        await pilot.pause()
        inp = app.screen.query_one("#path")
        inp.value = f"'{src}'"            # as a drag from Finder pastes it
        await pilot.pause()
        assert app.screen.files == [str(src)]
        app.screen.query_one("#add").press()
        await pilot.pause()
        assert len(app.queue.items) == 1 and app.queue.items[0].voice == "bm_george"
        assert started  # the queue starts by itself after adding
        assert q.Queue.load().items[0].source == str(src)


async def test_settings_save_and_validate(env):
    from fiatvox.settings import Settings
    from fiatvox.tui.app import FiatVox
    app = FiatVox()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        app.screen.query_one("#silence_paragraph").value = "99"
        app.screen.query_one("#save").press()
        await pilot.pause()
        assert "between 0 and 10" in str(app.screen.query_one("#msg").render())
        app.screen.query_one("#silence_paragraph").value = "0.6"
        app.screen.query_one("#review_stop").value = True
        app.screen.query_one("#save").press()
        await pilot.pause()
    s = Settings.load()
    assert s.silence_paragraph == 0.6 and s.review_stop


async def test_voices_list_every_voice(env):
    from fiatvox.narrate.kokoro_engine import VOICES
    from fiatvox.tui.app import FiatVox
    app = FiatVox()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("v")
        await pilot.pause()
        assert app.screen.query_one("#list").option_count == len(VOICES) == 28


async def test_review_stop_actions(env):
    tmp, started = env
    from fiatvox import bookqueue as q
    from fiatvox.tui.app import FiatVox
    qu = q.Queue()
    it = qu.add(str(tmp / "b.epub"), "bm_george", str(tmp / "out"), review_stop=True)
    it.status, it.preview, it.review_chapter = q.REVIEW, str(tmp / "p.m4a"), "Chapter One: Start"
    qu.save()
    app = FiatVox()
    async with app.run_test(size=(120, 50)) as pilot:
        assert app.query_one("#review").display
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#voice").value = "bm_lewis"
        app.screen.query_one("#voice_redo").press()
        await pilot.pause()
        item = app.queue.items[0]
        assert item.voice == "bm_lewis" and item.status == q.WAITING and not item.reviewed
        assert item.job().review_stop  # the first chapter is checked again in the new voice
        item.status = q.REVIEW
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#continue").press()
        await pilot.pause()
        assert item.reviewed and not item.job().review_stop and item.status == q.WAITING


async def test_refresh_while_a_dialog_is_open(env):
    from fiatvox.tui.app import FiatVox
    app = FiatVox()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("v")
        await pilot.pause()
        app.refresh_queue()   # the once-a-second refresh, with the voices dialog on top
        await pilot.pause(1.2)
