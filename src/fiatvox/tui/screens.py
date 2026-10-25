"""Dialogs: add books, voices, review stop, settings, AI setup, pronunciation helper."""
from __future__ import annotations

import threading
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, DataTable, Input, Label, OptionList, Select, Static)
from textual.widgets.option_list import Option

from .. import voices as vox
from ..narrate.kokoro_engine import VOICES
from ..settings import Settings
from . import pickers


def voice_options() -> list[tuple[str, str]]:
    return [(f"{v}  {VOICES[v][0]} {VOICES[v][1]}, grade {VOICES[v][2]}", v) for v in VOICES]


class Dialog(ModalScreen):
    """A modal with a title line and Escape to close."""
    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    Dialog { align: center middle; }
    Dialog > Vertical { width: 96; max-width: 100%; height: auto; max-height: 95%; border: thick $accent;
                        background: $panel; padding: 1 2; }
    Dialog .title { text-style: bold; margin-bottom: 1; }
    Dialog .hint { color: $text-muted; margin-bottom: 1; width: 100%; }
    Dialog .row { height: auto; margin-bottom: 1; }
    Dialog .row > Button { margin-right: 1; }
    Dialog Input { width: 1fr; }
    Dialog .error { color: $error; }
    Dialog .ok { color: $success; }
    """

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event) -> None:
        event.stop()  # never let a dialog's button reach the main screen behind it

    def on_data_table_row_selected(self, event) -> None:
        event.stop()


# ----------------------------------------------------------------------------
# add books
# ----------------------------------------------------------------------------

class AddBooks(Dialog):
    def __init__(self, settings: Settings, files: list[str] | None = None):
        super().__init__()
        self.st = settings
        self.files: list[str] = list(files or [])

    def on_mount(self) -> None:
        self._show()

    def on_paste(self, event) -> None:
        """A drag from Finder anywhere on this dialog, whichever field has the cursor."""
        paths = pickers.parse_paths(event.text)
        if paths and any(pickers.is_book(p) for p in paths):
            event.stop()
            event.prevent_default()
            self._added(paths)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Add books", classes="title")
            yield Label("EPUB, PDF (scanned is fine), RTF, Word, ODT or text. Choose them, type a path, or drag "
                        "files from Finder into this window.", classes="hint")
            with Horizontal(classes="row"):
                yield Button("Choose books...", id="choose", variant="primary")
                yield Input(placeholder="or type or drag a path here, then press Return", id="path")
            yield Static("No books chosen yet.", id="files")
            yield Label("Voice")
            yield Select(voice_options(), value=self.st.default_voice, allow_blank=False, id="voice")
            yield Label("Save the audiobooks in")
            with Horizontal(classes="row"):
                yield Input(value=self.st.output_dir, id="out")
                yield Button("Choose folder...", id="folder")
            yield Checkbox("Review stop: pause after the first chapter so I can check it", self.st.review_stop,
                           id="review")
            yield Static("", id="msg", classes="error")
            with Horizontal(classes="row"):
                yield Button("Add to queue", id="add", variant="success")
                yield Button("Cancel", id="cancel")

    def _show(self) -> None:
        self.query_one("#files", Static).update(
            "\n".join(f"  {Path(f).name}" for f in self.files) if self.files else "No books chosen yet.")

    @work(thread=True)
    def pick_books(self) -> None:
        chosen = pickers.choose_books()
        self.app.call_from_thread(self._added, chosen)

    @work(thread=True)
    def pick_folder(self) -> None:
        folder = pickers.choose_folder()
        if folder:
            self.app.call_from_thread(lambda: setattr(self.query_one("#out", Input), "value", folder))

    def _added(self, paths: list[str]) -> None:
        bad = [p for p in paths if not pickers.is_book(p)]
        for p in paths:
            if pickers.is_book(p) and p not in self.files:
                self.files.append(p)
        self.query_one("#msg", Static).update(
            ("Not a book file Fiat Vox can read: " + ", ".join(Path(b).name for b in bad)) if bad else "")
        self._show()

    @on(Button.Pressed, "#choose")
    def _choose(self) -> None:
        self.pick_books()

    @on(Button.Pressed, "#folder")
    def _folder(self) -> None:
        self.pick_folder()

    @on(Input.Submitted, "#path")
    def _typed(self, ev: Input.Submitted) -> None:
        self._added(pickers.parse_paths(ev.value))
        ev.input.value = ""

    @on(Input.Changed, "#path")
    def _dropped(self, ev: Input.Changed) -> None:
        # a drag from Finder pastes the whole path at once; take it as soon as it names a real book
        paths = pickers.parse_paths(ev.value)
        if paths and all(pickers.is_book(p) for p in paths):
            self._added(paths)
            ev.input.value = ""

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#add")
    def _add(self) -> None:
        if not self.files:
            self.query_one("#msg", Static).update("Choose at least one book first.")
            return
        out = self.query_one("#out", Input).value.strip()
        if not out:
            self.query_one("#msg", Static).update("Choose where to save the audiobooks.")
            return
        self.dismiss({"files": self.files, "voice": self.query_one("#voice", Select).value,
                      "out": str(Path(out).expanduser()), "review": self.query_one("#review", Checkbox).value})


# ----------------------------------------------------------------------------
# voices
# ----------------------------------------------------------------------------

class Voices(Dialog):
    """Every English voice; Return plays its sample; D makes it the default."""
    BINDINGS = [("escape", "close", "Close"), ("d", "default", "Make default")]
    DEFAULT_CSS = """
    Voices #list { max-height: 20; }
    """

    def on_mount(self) -> None:
        ol = self.query_one("#list", OptionList)
        ol.highlighted = ol.get_option_index(self.st.default_voice)
        ol.focus()

    def __init__(self, settings: Settings, choose: bool = False):
        super().__init__()
        self.st = settings
        self.choose = choose

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Voices", classes="title")
            yield Label("Return plays a five-second sample. D makes the voice the default for new books."
                        + (" Choose one with the button below." if self.choose else ""), classes="hint")
            opts = []
            for accent in ("American", "British"):
                for v, (acc, gender, grade) in VOICES.items():
                    if acc == accent:
                        star = "  (default)" if v == self.st.default_voice else ""
                        opts.append(Option(f"{v:<12} {acc:<8} {gender:<6}  grade {grade:<3}{star}", id=v))
            yield OptionList(*opts, id="list")
            yield Static("", id="msg")
            with Horizontal(classes="row"):
                if self.choose:
                    yield Button("Use this voice", id="use", variant="success")
                yield Button("Close", id="close")

    def _selected(self) -> str | None:
        ol = self.query_one("#list", OptionList)
        return ol.get_option_at_index(ol.highlighted).id if ol.highlighted is not None else None

    @work(thread=True, exclusive=True)
    def play(self, v: str) -> None:
        if not vox.has_sample(v):
            self.app.call_from_thread(self.query_one("#msg", Static).update, f"Making the sample for {v}...")
        vox.play(v)
        self.app.call_from_thread(self.query_one("#msg", Static).update, f"Playing {v}: {vox.describe(v)}")

    @on(OptionList.OptionSelected)
    def _play(self, ev: OptionList.OptionSelected) -> None:
        self.play(ev.option.id)

    def action_default(self) -> None:
        v = self._selected()
        if v:
            self.st.default_voice = v
            self.st.save()
            self.query_one("#msg", Static).update(f"{v} is now the default voice.")

    @on(Button.Pressed, "#use")
    def _use(self) -> None:
        vox.stop()
        self.dismiss(self._selected())

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        vox.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        vox.stop()
        self.dismiss(None)


# ----------------------------------------------------------------------------
# review stop
# ----------------------------------------------------------------------------

class Review(Dialog):
    """Shown when a book pauses after its first chapter. Returns an action dict for the app to carry out."""

    def __init__(self, item):
        super().__init__()
        self.item = item
        self.player = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Review: {self.item.name}", classes="title")
            yield Label(f"Listen to \"{self.item.review_chapter}\" in voice {self.item.voice}, then choose.",
                        classes="hint")
            with Horizontal(classes="row"):
                yield Button("Play the chapter", id="play", variant="primary")
                yield Button("Stop playing", id="stopplay")
            with Horizontal(classes="row"):
                yield Button("Continue with the rest of the book", id="continue", variant="success")
            with Horizontal(classes="row"):
                yield Select(voice_options(), value=self.item.voice, allow_blank=False, id="voice")
                yield Button("Switch voice and redo", id="voice_redo")
            with Horizontal(classes="row"):
                yield Button("Add a pronunciation fix and redo", id="pronounce")
            yield Label("A note for the AI (for example: \"chapter titles are read twice\")")
            with Horizontal(classes="row"):
                yield Input(placeholder="What should be fixed?", id="note")
                yield Button("Send to the AI and redo", id="note_send")
            with Horizontal(classes="row"):
                yield Button("Cancel this book", id="cancel", variant="error")
                yield Button("Decide later", id="later")

    def _stop(self) -> None:
        if self.player and self.player.poll() is None:
            self.player.terminate()

    @on(Button.Pressed)
    def _pressed(self, ev: Button.Pressed) -> None:
        import subprocess
        b = ev.button.id
        if b == "play":
            self._stop()
            self.player = subprocess.Popen(["afplay", self.item.preview])
            return
        if b == "stopplay":
            self._stop()
            return
        self._stop()
        if b == "continue":
            self.dismiss({"action": "continue"})
        elif b == "voice_redo":
            self.dismiss({"action": "voice", "voice": self.query_one("#voice", Select).value})
        elif b == "pronounce":
            self.dismiss({"action": "pronounce"})
        elif b == "note_send":
            note = self.query_one("#note", Input).value.strip()
            if note:
                self.dismiss({"action": "note", "note": note})
        elif b == "cancel":
            self.dismiss({"action": "cancel"})
        elif b == "later":
            self.dismiss(None)

    def action_close(self) -> None:
        self._stop()
        self.dismiss(None)


# ----------------------------------------------------------------------------
# pronunciation helper
# ----------------------------------------------------------------------------

class Pronounce(Dialog):
    def __init__(self, settings: Settings, voice: str | None = None):
        super().__init__()
        self.st = settings
        self.voice = voice or settings.default_voice

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Fix a pronunciation", classes="title")
            yield Label("Type the word and how it should sound. The AI proposes phonemes; Fiat Vox checks them, "
                        "lets you hear the result, and saves only when you approve.", classes="hint")
            with Grid(id="g"):
                yield Label("Word")
                yield Input(placeholder="quasi", id="word")
                yield Label("Should sound like")
                yield Input(placeholder="kwah-zee", id="resp")
                yield Label("Voice")
                yield Select(voice_options(), value=self.voice, allow_blank=False, id="voice")
                yield Label("Phonemes")
                yield Input(placeholder="proposed by the AI, or type them", id="ph")
            yield Static("", id="msg")
            with Horizontal(classes="row"):
                yield Button("Propose", id="propose", variant="primary")
                yield Button("Hear it now", id="current")
                yield Button("Hear the fix", id="new")
                yield Button("Save", id="save", variant="success")
                yield Button("Close", id="close")

    DEFAULT_CSS = """
    Pronounce #g { grid-size: 2; grid-columns: 20 1fr; height: auto; grid-gutter: 0 1; }
    """

    def _vals(self):
        v = self.query_one("#voice", Select).value
        return (self.query_one("#word", Input).value.strip(), self.query_one("#resp", Input).value.strip(),
                self.query_one("#ph", Input).value.strip().strip("/"), v, v[0])

    def _msg(self, text: str) -> None:
        self.app.call_from_thread(self.query_one("#msg", Static).update, text)

    @work(thread=True, exclusive=True)
    def propose(self) -> None:
        from ..ai import get_backend
        from ..narrate import helper
        word, resp, _, _, accent = self._vals()
        if not word or not resp:
            self._msg("Type the word and how it should sound.")
            return
        backend = get_backend(self.st.ai_provider, self.st.ai_model)
        if not backend.available()[0]:
            self._msg(f"No AI available ({backend.available()[1]}). Type the phonemes yourself; current reading: "
                      f"/{helper.current_reading(word, accent)}/")
            return
        self._msg("Asking the AI...")
        try:
            ph, why = helper.propose(backend, word, resp, accent)
        except Exception as e:
            self._msg(f"The AI could not propose phonemes: {e}")
            return
        self.app.call_from_thread(lambda: setattr(self.query_one("#ph", Input), "value", ph))
        c = helper.check(word, ph, accent)
        self._msg(f"Now: /{helper.current_reading(word, accent)}/   Proposed: /{ph}/   {why}\n"
                  + ("Checked: misaki accepts it. Press Hear the fix." if c.ok else "Problem: " + "; ".join(c.problems)))

    @work(thread=True, exclusive=True)
    def hear(self, new: bool) -> None:
        from ..narrate import helper
        word, _, ph, voice, accent = self._vals()
        if not word:
            self._msg("Type the word first.")
            return
        if new:
            c = helper.check(word, ph, accent)
            if not c.ok:
                self._msg("Problem: " + "; ".join(c.problems))
                return
        self._msg("Speaking...")
        helper.play(helper.clip(word, ph if new else None, voice), wait=True)
        self._msg("")

    @on(Button.Pressed)
    def _pressed(self, ev: Button.Pressed) -> None:
        from ..narrate import helper
        b = ev.button.id
        if b == "propose":
            self.propose()
        elif b == "current":
            self.hear(False)
        elif b == "new":
            self.hear(True)
        elif b == "save":
            word, resp, ph, voice, accent = self._vals()
            c = helper.check(word, ph, accent)
            if not c.ok:
                self.query_one("#msg", Static).update("Not saved: " + "; ".join(c.problems))
                return
            path = helper.save(word, ph, accent, resp)
            self.query_one("#msg", Static).update(f"Saved for every {'American' if accent == 'a' else 'British'} "
                                                  f"voice ({path.name}).")
            self.saved = True
        elif b == "close":
            self.dismiss(getattr(self, "saved", False))

    def action_close(self) -> None:
        self.dismiss(getattr(self, "saved", False))


# ----------------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------------

class SettingsDialog(Dialog):
    def __init__(self, settings: Settings):
        super().__init__()
        self.st = settings

    def compose(self) -> ComposeResult:
        from ..ai import get_backend
        s = self.st
        with Vertical():
            yield Label("Settings", classes="title")
            with VerticalScroll():
                yield Label(f"AI: {get_backend(s.ai_provider, s.ai_model).label}")
                yield Button("Change AI or sign in...", id="setup")
                yield Label("Review rounds (the AI checks its own work, 0 to 5)")
                yield Input(str(s.review_rounds), id="review_rounds", type="integer")
                yield Label("Silence before a chapter title, after it, between paragraphs, at a chapter's end (seconds)")
                with Horizontal(classes="row"):
                    yield Input(str(s.silence_before_title), id="silence_before_title", type="number")
                    yield Input(str(s.silence_after_title), id="silence_after_title", type="number")
                    yield Input(str(s.silence_paragraph), id="silence_paragraph", type="number")
                    yield Input(str(s.silence_chapter_end), id="silence_chapter_end", type="number")
                yield Label("Default voice")
                yield Select(voice_options(), value=s.default_voice, allow_blank=False, id="default_voice")
                yield Label("Save audiobooks in")
                yield Input(s.output_dir, id="output_dir")
                yield Checkbox("Review stop on for new books", s.review_stop, id="review_stop")
                yield Checkbox("Write a QC report next to each audiobook", s.qc_report, id="qc_report")
                yield Checkbox("Import finished audiobooks into Apple Books", s.import_to_books, id="import_to_books")
                yield Checkbox("Notify me when a book is ready", s.notify, id="notify")
                yield Label("Audio quality (kbps)")
                yield Select([("48", 48), ("64 (recommended)", 64), ("96", 96), ("128", 128)], value=s.bitrate_kbps,
                             allow_blank=False, id="bitrate_kbps")
                yield Label("Text recognition for scanned PDFs")
                yield Select([("Apple Vision (recommended)", "vision"), ("Tesseract", "tesseract")],
                             value=s.ocr_engine, allow_blank=False, id="ocr_engine")
            yield Static("", id="msg", classes="error")
            with Horizontal(classes="row"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#setup")
    def _setup(self) -> None:
        self.app.push_screen(Setup(self.st))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        s = self.st
        try:
            s.review_rounds = max(0, min(5, int(self.query_one("#review_rounds", Input).value or 0)))
            for k in ("silence_before_title", "silence_after_title", "silence_paragraph", "silence_chapter_end"):
                v = float(self.query_one(f"#{k}", Input).value)
                if not 0 <= v <= 10:
                    raise ValueError(f"{k.replace('_', ' ')} must be between 0 and 10 seconds")
                setattr(s, k, v)
        except ValueError as e:
            self.query_one("#msg", Static).update(str(e))
            return
        s.default_voice = self.query_one("#default_voice", Select).value
        s.output_dir = self.query_one("#output_dir", Input).value.strip() or s.output_dir
        for k in ("review_stop", "qc_report", "import_to_books", "notify"):
            setattr(s, k, self.query_one(f"#{k}", Checkbox).value)
        s.bitrate_kbps = self.query_one("#bitrate_kbps", Select).value
        s.ocr_engine = self.query_one("#ocr_engine", Select).value
        s.save()
        self.dismiss(True)


# ----------------------------------------------------------------------------
# AI setup (first launch, and from Settings)
# ----------------------------------------------------------------------------

class Setup(Dialog):
    """Pick how Fiat Vox reaches an AI model and sign in, without typing any Pi command."""

    def __init__(self, settings: Settings):
        super().__init__()
        self.st = settings
        self.answer: dict = {}
        self.answer_ready = threading.Event()

    def compose(self) -> ComposeResult:
        from ..ai.setup import CLAUDE_NOTE
        with Vertical():
            yield Label("AI setup", classes="title")
            yield Label("Fiat Vox uses an AI model to decide where chapters start, what to cut, and to fix "
                        "scanning errors. It works without one too, with simpler local rules.", classes="hint")
            yield Label(CLAUDE_NOTE, classes="hint")
            yield OptionList(id="choices")
            yield Static("", id="msg")
            with Horizontal(classes="row", id="promptrow"):
                yield Input(id="prompt", placeholder="")
                yield Button("Send", id="send")
            with Horizontal(classes="row"):
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#promptrow").display = False
        self.load_choices()

    @work(thread=True)
    def load_choices(self) -> None:
        from ..ai.setup import choices
        cs = choices()
        self.choice_list = cs

        def fill():
            ol = self.query_one("#choices", OptionList)
            ol.clear_options()
            for i, c in enumerate(cs):
                ol.add_option(Option(c.label, id=str(i)))
        self.app.call_from_thread(fill)

    def _msg(self, text: str) -> None:
        self.app.call_from_thread(self.query_one("#msg", Static).update, text)

    @on(OptionList.OptionSelected, "#choices")
    def _chosen(self, ev: OptionList.OptionSelected) -> None:
        self.run_choice(self.choice_list[int(ev.option.id)])

    def _ask(self, message: str, secret: bool = False, options=None) -> str | None:
        """Ask in the dialog from the sign-in thread and wait for the answer."""
        self.answer_ready.clear()

        def show():
            if options:
                message_full = message + "\n" + "\n".join(f"  {i}. {o['label']}" for i, o in enumerate(options, 1))
            else:
                message_full = message
            self.query_one("#msg", Static).update(message_full)
            row = self.query_one("#promptrow")
            row.display = True
            inp = self.query_one("#prompt", Input)
            inp.password = secret
            inp.value = ""
            inp.placeholder = "number" if options else ("paste here" if secret else "")
            inp.focus()
        self.app.call_from_thread(show)
        self.answer_ready.wait()
        self.app.call_from_thread(lambda: setattr(self.query_one("#promptrow"), "display", False))
        val = self.answer.get("value")
        if options and val is not None:
            try:
                return options[int(val) - 1]["id"]
            except (ValueError, IndexError):
                return None
        return val

    @on(Button.Pressed, "#send")
    @on(Input.Submitted, "#prompt")
    def _send(self) -> None:
        self.answer = {"value": self.query_one("#prompt", Input).value.strip()}
        self.answer_ready.set()

    @work(thread=True, exclusive=True)
    def run_choice(self, c) -> None:
        import webbrowser

        from ..ai import get_backend, pi_backend
        from ..ai.setup import pick_model, save_direct_key
        st = self.st
        if c.kind == "none":
            st.ai_provider, st.ai_model = "none", ""
        elif c.kind == "claude-code":
            st.ai_provider, st.ai_model = "claude-code", "sonnet"
        elif c.kind == "direct":
            key = self._ask(f"Paste your {c.label.split(' key')[0]} key. It is stored in your Mac's Keychain.",
                            secret=True)
            if not key:
                self._msg("No key entered; nothing changed.")
                return
            save_direct_key(c.provider, key)
            st.ai_provider, st.ai_model = c.provider, ""
        else:
            def on_event(ev):
                kind = ev.get("event")
                if kind == "auth_url":
                    webbrowser.open(ev["url"])
                    self._msg("Your browser has opened to sign in. Finish there; Fiat Vox continues by itself.\n"
                              + ev["url"])
                elif kind == "device_code":
                    webbrowser.open(ev["verificationUri"])
                    self._msg(f"Visit {ev['verificationUri']} and enter the code {ev['userCode']}")
                elif kind in ("info", "progress"):
                    self._msg(ev.get("message", ""))
                elif kind == "prompt_withdrawn":
                    self.answer = {"value": None}
                    self.answer_ready.set()
                elif kind == "prompt":
                    t = ev.get("type")
                    if t == "select":
                        return self._ask(ev["message"], options=ev["options"])
                    return self._ask(ev["message"], secret=(t == "secret"))
                return None
            try:
                pi_backend.login(c.provider, "oauth" if c.kind == "pi-oauth" else "api_key", on_event)
            except Exception as e:
                self._msg(f"Sign-in did not finish: {e}")
                return
            st.ai_provider, st.ai_model = "pi", pick_model(c.provider, pi_backend.models())
        st.setup_done = True
        st.save()
        backend = get_backend(st.ai_provider, st.ai_model)
        if st.ai_provider == "none":
            self._msg("Saved: no AI. Fiat Vox will use its local rules.")
            return
        self._msg(f"Saved: {backend.label}. Checking it works...")
        ok, why = backend.available()
        if not ok:
            self._msg(f"Saved, but not ready yet: {why}")
            return
        try:
            backend.complete('Reply with exactly this JSON: {"ok": true}', purpose="test", timeout=120)
            self._msg(f"Saved and working: {backend.label}. You can close this window.")
        except Exception as e:
            self._msg(f"Saved, but a test request failed: {e}")

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.answer = {"value": None}
        self.answer_ready.set()
        self.dismiss(True)

    def action_close(self) -> None:
        self._close()


class Confirm(Dialog):
    def __init__(self, question: str, yes: str = "Yes", no: str = "No"):
        super().__init__()
        self.question, self.yes, self.no = question, yes, no

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.question)
            with Horizontal(classes="row"):
                yield Button(self.yes, id="yes", variant="warning")
                yield Button(self.no, id="no")

    @on(Button.Pressed)
    def _pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(ev.button.id == "yes")


class ErrorsDialog(Dialog):
    def compose(self) -> ComposeResult:
        from ..troubleshoot import list_reports
        with Vertical():
            yield Label("Error reports", classes="title")
            yield Label("Every failure is written here, with the AI's diagnosis, so the cause can be fixed in the "
                        "code. Press Return on a report to open it.", classes="hint")
            t = DataTable(id="t", cursor_type="row")
            t.add_columns("When", "Book", "Outcome", "Error")
            self.reports = list_reports(50)
            for r in self.reports:
                t.add_row(r["when"].replace("T", " "), r["book"][:30], r["outcome"][:30], r["first_error"][:50])
            yield t
            yield Button("Close", id="close")

    @on(DataTable.RowSelected)
    def _open(self, ev: DataTable.RowSelected) -> None:
        import subprocess
        subprocess.Popen(["open", self.reports[ev.cursor_row]["file"]])

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)
