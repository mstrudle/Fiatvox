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


async def run_cmd(app, pilot, text):
    """Type a command on the command line and press Return."""
    app.main("#cmd").value = text
    app.main("#cmd").focus()
    await pilot.press("enter")
    await pilot.pause()


async def test_add_book_by_path(env):
    tmp, started = env
    from fiatvox import bookqueue as q
    from fiatvox.tui.app import FiatVox
    src = tmp / "My Book.epub"
    make_epub(src)
    app = FiatVox()
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#empty").display
        await run_cmd(app, pilot, "/add")
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
        await run_cmd(app, pilot, "/settings")
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
        await run_cmd(app, pilot, "/voices")
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
        await run_cmd(app, pilot, "/review")
        app.screen.query_one("#voice").value = "bm_lewis"
        app.screen.query_one("#voice_redo").press()
        await pilot.pause()
        item = app.queue.items[0]
        assert item.voice == "bm_lewis" and item.status == q.WAITING and not item.reviewed
        assert item.job().review_stop  # the first chapter is checked again in the new voice
        item.status = q.REVIEW
        await run_cmd(app, pilot, "/queue")
        await run_cmd(app, pilot, "")          # on the Queue tab, Return on an empty line opens the selected book
        app.screen.query_one("#continue").press()
        await pilot.pause()
        assert item.reviewed and not item.job().review_stop and item.status == q.WAITING


async def test_refresh_while_a_dialog_is_open(env):
    from fiatvox.tui.app import FiatVox
    app = FiatVox()
    async with app.run_test(size=(120, 40)) as pilot:
        await run_cmd(app, pilot, "/voices")
        app.refresh_queue()   # the once-a-second refresh, with the voices dialog on top
        await pilot.pause(1.2)


async def test_dialog_cancel_does_not_reach_the_running_book(env, monkeypatch):
    from fiatvox import runner
    from fiatvox.tui import screens
    from fiatvox.tui.app import FiatVox
    monkeypatch.setattr(runner.Runner, "running", property(lambda self: True))
    cancelled = []
    monkeypatch.setattr(runner.Runner, "cancel_current", lambda self: cancelled.append(True))
    app = FiatVox()
    async with app.run_test(size=(120, 40)) as pilot:
        await run_cmd(app, pilot, "/add")
        app.screen.query_one("#cancel").press()
        await pilot.pause()
        assert not isinstance(app.screen, (screens.AddBooks, screens.Confirm)), type(app.screen)
        assert not cancelled


async def test_drop_a_file_on_the_main_screen(env):
    from textual import events

    from fiatvox.tui import screens
    from fiatvox.tui.app import FiatVox
    tmp, _ = env
    src = tmp / "Dropped Book.epub"
    make_epub(src)
    app = FiatVox()
    async with app.run_test(size=(120, 40)) as pilot:
        app.main("#cmd").post_message(events.Paste(str(src).replace(" ", "\\ ")))   # how Terminal pastes a drag
        await pilot.pause()
        assert isinstance(app.screen, screens.AddBooks) and app.screen.files == [str(src)]


async def test_drop_while_the_choose_button_has_focus(env):
    from textual import events

    from fiatvox.tui.app import FiatVox
    tmp, _ = env
    src = tmp / "Second Book.epub"
    make_epub(src)
    app = FiatVox()
    async with app.run_test(size=(120, 40)) as pilot:
        await run_cmd(app, pilot, "/add")
        app.screen.query_one("#choose").focus()
        app.screen.post_message(events.Paste(f"'{src}'"))
        await pilot.pause()
        assert app.screen.files == [str(src)]


async def test_default_voice_marker_moves(env):
    from fiatvox.settings import Settings
    from fiatvox.tui.app import FiatVox
    app = FiatVox()
    async with app.run_test(size=(120, 50)) as pilot:
        await run_cmd(app, pilot, "/voices")
        ol = app.screen.query_one("#list")
        ol.highlighted = ol.get_option_index("bm_lewis")
        await pilot.press("d")
        await pilot.pause()
        assert "(default)" in str(ol.get_option("bm_lewis").prompt)
        assert "(default)" not in str(ol.get_option("bm_george").prompt)
    assert Settings.load().default_voice == "bm_lewis"


async def test_new_session_clears_finished_books(env):
    tmp, _ = env
    from fiatvox import bookqueue as q
    from fiatvox.tui.app import FiatVox
    qu = q.Queue()
    done = qu.add(str(tmp / "a.epub"), "bm_george", str(tmp))
    waiting = qu.add(str(tmp / "b.epub"), "bm_george", str(tmp))
    done.status = q.DONE
    qu.save()
    app = FiatVox()
    async with app.run_test(size=(120, 45)) as pilot:
        await run_cmd(app, pilot, "/new")
        assert [i.id for i in app.queue.items] == [waiting.id]
    assert [i.id for i in q.Queue.load().items] == [waiting.id]


async def test_unknown_command_and_help(env):
    from fiatvox.tui import screens
    from fiatvox.tui.app import FiatVox
    app = FiatVox()
    async with app.run_test(size=(120, 45)) as pilot:
        await run_cmd(app, pilot, "/frobnicate")
        assert not isinstance(app.screen, screens.Dialog)
        await run_cmd(app, pilot, "/help")
        assert isinstance(app.screen, screens.HelpDialog)


async def test_book_dragged_onto_the_command_line(env):
    tmp, _ = env
    from fiatvox.tui import screens
    from fiatvox.tui.app import FiatVox
    src = tmp / "Dragged In.epub"
    make_epub(src)
    app = FiatVox()
    async with app.run_test(size=(120, 45)) as pilot:
        app.main("#cmd").value = str(src).replace(" ", "\\ ")
        await pilot.pause()
        assert isinstance(app.screen, screens.AddBooks) and app.screen.files == [str(src)]


def test_commands_and_paths_are_told_apart():
    from fiatvox.tui.app import is_command
    assert is_command("/add") and is_command("/new ") and is_command("/add ~/Books/x.epub") and is_command("/?")
    assert not is_command("/Users/me/Books/On War.epub") and not is_command("/Volumes/x/y.pdf")
    assert not is_command("~/Books/x.epub")


async def test_now_tab_shows_the_current_book(env):
    """A book starting switches to the Now tab; telemetry, the log and the chapter table follow its events."""
    tmp, _ = env
    from textual.widgets import DataTable, TabbedContent
    from fiatvox import bookqueue as q
    from fiatvox.tui.app import FiatVox
    qu = q.Queue()
    it = qu.add(str(tmp / "War.epub"), "bm_george", str(tmp))
    qu.save()
    app = FiatVox()
    async with app.run_test(size=(140, 45)) as pilot:
        item = app.queue.items[0]
        item.status = q.PREPARING
        events = [{"type": "status"},
                  {"type": "stage", "stage": "Deciding", "frac": 0.0, "msg": "Asking the AI where chapters start"},
                  {"type": "stage", "stage": "OCR", "frac": 0.5, "msg": "OCR page 3 of 6"},
                  {"type": "prepared", "title": "On War", "author": "Clausewitz", "chapters": 2, "words": 900,
                   "voice": "bm_george", "out_dir": str(tmp), "ai": "Claude Code (sonnet)", "seconds": 3},
                  {"type": "narrating", "chapter": 1, "chapters": 2, "title": "Book One", "unit": 3, "units": 10,
                   "done": 300, "total": 1000, "eta": 70, "speed": 12.0},
                  {"type": "chapter_done", "chapter": 1, "chapters": 2, "title": "Book One", "duration": 95.0,
                   "from_cache": False}]
        for ev in events:
            app._tele_event(item, ev)
        await pilot.pause()
        assert app.main("#tabs", TabbedContent).active == "now"
        assert "On War" in str(app.main("#now_head").render()) and "Clausewitz" in str(app.main("#now_head").render())
        assert "(1/2 chapters)" in str(app.main("#overall_txt").render())
        assert "12x real time" in str(app.main("#overall_txt").render())
        assert app.main("#chapters", DataTable).row_count == 1
        log = "\n".join(str(line) for line in app.main("#log").lines)
        assert "Asking the AI where chapters start" in log and "OCR page" not in log


async def test_buttons_add_and_review(env):
    tmp, _ = env
    from fiatvox import bookqueue as q
    from fiatvox.tui import screens
    from fiatvox.tui.app import FiatVox
    qu = q.Queue()
    it = qu.add(str(tmp / "b.epub"), "bm_george", str(tmp), review_stop=True)
    it.status, it.preview, it.review_chapter = q.REVIEW, str(tmp / "p.m4a"), "Chapter One"
    qu.save()
    app = FiatVox()
    async with app.run_test(size=(140, 45)) as pilot:
        assert app.main("#main_review").display and app.main("#main_cancel").disabled
        app.main("#main_add").press()
        await pilot.pause()
        assert isinstance(app.screen, screens.AddBooks)
        app.screen.query_one("#cancel").press()
        await pilot.pause()
        app.main("#main_review").press()
        await pilot.pause()
        assert isinstance(app.screen, screens.Review)
