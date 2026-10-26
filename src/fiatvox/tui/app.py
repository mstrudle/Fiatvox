"""Fiat Vox's terminal app: the queue, progress, and every action one command away."""
from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Label, ProgressBar, Static

from .. import __version__
from .. import bookqueue as q
from ..runner import Runner
from ..settings import Settings
from . import screens


def fmt_eta(sec) -> str:
    if sec is None:
        return ""
    sec = int(sec)
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s"


FONT = {  # 5 rows high; "#" is a filled pixel
    "F": ["#####", "#    ", "#### ", "#    ", "#    "],
    "I": ["###", " # ", " # ", " # ", "###"],
    "A": [" ### ", "#   #", "#####", "#   #", "#   #"],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  "],
    "V": ["#   #", "#   #", "#   #", " # # ", "  #  "],
    "O": [" ### ", "#   #", "#   #", "#   #", " ### "],
    "X": ["#   #", " # # ", "  #  ", " # # ", "#   #"],
    " ": ["   ", "   ", "   ", "   ", "   "],
}


def banner(text: str = "FIAT VOX", wide: bool = True) -> str:
    """The name in block letters, like Feynman's: each pixel two characters wide when there is room."""
    cell = "\u2588\u2588" if wide else "\u2588"
    blank = "  " if wide else " "
    rows = []
    for r in range(5):
        rows.append((blank).join("".join(cell if ch == "#" else blank for ch in FONT[c][r]) for c in text))
    return "\n".join(rows)


COMMANDS = [
    ("/add", "add books (or drag them here)"),
    ("/start", "start or resume the queue"),
    ("/cancel", "stop the current book"),
    ("/review", "check a paused first chapter"),
    ("/open", "show an audiobook in Finder"),
    ("/remove", "take a book off the list"),
    ("/new", "clear finished books"),
    ("/voices", "hear and choose voices"),
    ("/pronounce", "fix how a word is said"),
    ("/settings", "AI, pauses, folders"),
    ("/errors", "reports of failures"),
    ("/help", "all commands and keys"),
    ("/quit", "quit; work resumes later"),
]
COMMAND_RE = __import__("re").compile(r"^/[A-Za-z?]+(?:\s|$)")  # "/add", "/new"; a path like "/Users/..." is not one


def is_command(text: str) -> bool:
    return bool(COMMAND_RE.match(text.strip()))


ALIASES = {"/a": "/add", "/s": "/start", "/c": "/cancel", "/r": "/review", "/v": "/voices", "/p": "/pronounce",
           "/t": "/settings", "/e": "/errors", "/q": "/quit", "/exit": "/quit", "/h": "/help", "/?": "/help",
           "/up": "/up", "/down": "/down", "/clear": "/new"}


class FiatVox(App):
    TITLE = f"Fiat Vox {__version__}"
    SUB_TITLE = ""
    CSS = """
    Screen { background: #000000; }
    #banner { color: #9fbf6f; content-align: center middle; width: 100%; height: auto; margin-top: 1; }
    #box { border: round #5a6a4a; border-title-align: center; border-title-color: #9fbf6f; height: auto;
           margin: 0 2; padding: 0 1; }
    #info { width: 46; height: auto; padding-right: 2; border-right: solid #3a4530; }
    #cmds { width: 1fr; height: auto; padding-left: 2; }
    #info, #cmds Static { height: auto; }
    #empty { margin: 1 4; color: $text-muted; }
    #queue { height: 1fr; min-height: 4; background: #000000; margin: 1 0 0 0; overflow-x: hidden; }
    #detail { height: auto; max-height: 8; border-top: solid #5a6a4a; padding: 0 1; }
    #bars { height: auto; padding: 0 1; }
    #bars Horizontal { height: 1; }
    #bars Label { width: 11; }
    #bars ProgressBar { width: 1fr; }
    #cmd { margin: 0 0; border: tall #3a4530; background: #000000; }
    #cmd:focus { border: tall #9fbf6f; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit_app", "Quit", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False, priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.st = Settings.load()
        self.queue = q.Queue.load()
        self.queue.save()
        self.runner = Runner(self.queue, self._event)

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        from textual.suggester import SuggestFromList
        from textual.widgets import Input
        yield Static(banner(), id="banner")
        with Horizontal(id="box"):
            yield Static("", id="info")
            with Vertical(id="cmds"):
                yield Static("[b #9fbf6f]Commands[/]")
                yield Static(self._commands_text())
        yield Static("No books yet. Type /add, or drag a book from Finder onto this window.", id="empty")
        t = DataTable(id="queue", cursor_type="row", zebra_stripes=False)
        t.add_columns("Book", "Voice", "Review", "Status", "Progress", "Time left")
        yield t
        with Vertical(id="bars"):
            with Horizontal():
                yield Label("This book")
                yield ProgressBar(total=100, show_eta=False, id="book_bar")
            with Horizontal():
                yield Label("All books")
                yield ProgressBar(total=100, show_eta=False, id="all_bar")
        yield Static("", id="detail")
        yield Input(placeholder="Type / for a command, or drag a book here. Up and down choose a book; "
                    "Return opens it.", id="cmd",
                    suggester=SuggestFromList([c for c, _ in COMMANDS] + ["/up", "/down"], case_sensitive=False))
        yield Static("", id="status")

    @staticmethod
    def _commands_text():
        """Commands in two columns, never wrapped."""
        from rich.table import Table
        t = Table.grid(padding=(0, 2))
        for _ in range(2):
            t.add_column(style="#9fbf6f", no_wrap=True, min_width=10)
            t.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        half = (len(COMMANDS) + 1) // 2
        for i in range(half):
            a = COMMANDS[i]
            b = COMMANDS[half + i] if half + i < len(COMMANDS) else ("", "")
            t.add_row(a[0], a[1], b[0], b[1])
        return t

    def _info_text(self) -> str:
        from ..ai import get_backend
        b = get_backend(self.st.ai_provider, self.st.ai_model)
        items = self.queue.items
        waiting = sum(1 for i in items if i.status == q.WAITING)
        done = sum(1 for i in items if i.status == q.DONE)
        review = sum(1 for i in items if i.status == q.REVIEW)
        cur = self.runner.current
        now = f"{cur.name[:26]} {100 * cur.progress:.0f}%" if cur else "nothing"
        out = self.st.output_dir.replace(str(Path.home()), "~")
        rows = [("AI", b.label), ("voice", self.st.default_voice), ("save to", out), ("working on", now),
                ("queue", f"{waiting} waiting, {review} to review, {done} done")]
        room = 31 if self.size.width >= 140 else 22
        return "\n".join(f"[#8a8a8a]{k:<11}[/]{v[:room]}" for k, v in rows)

    def on_resize(self) -> None:
        tall, wide = self.size.height >= 34, self.size.width >= 100
        self.main("#banner").display = tall
        self.main("#banner", Static).update(banner(wide=wide))
        self.main("#box").display = self.size.height >= 26
        self.main("#info").styles.width = 46 if self.size.width >= 140 else 36

    def main(self, selector: str, kind=None):
        """A widget on the main screen, even while a dialog is open on top of it."""
        base = self.screen_stack[0]
        return base.query_one(selector, kind) if kind else base.query_one(selector)

    def on_mount(self) -> None:
        self.main("#box").border_title = f"Fiat Vox {__version__}"
        self.main("#cmd").focus()
        self.refresh_queue()
        self.set_interval(1.0, self.refresh_queue)
        if not self.st.setup_done:
            self.push_screen(screens.Setup(self.st), lambda _: self._reload_settings())

    def _reload_settings(self) -> None:
        self.st = Settings.load()

    # ------------------------------------------------------------------ queue display
    def refresh_queue(self) -> None:
        t = self.main("#queue", DataTable)
        keep = t.cursor_row
        t.clear()
        for it in self.queue.items:
            pct = f"{100 * it.progress:3.0f}%" if it.status in q.ACTIVE + (q.DONE,) else ""
            t.add_row(it.name[:48], it.voice, "yes" if it.review_stop else "", q.STATUS_LABEL[it.status], pct,
                      fmt_eta(it.eta) if it.status in q.ACTIVE else "", key=it.id)
        self.main("#empty").display = not self.queue.items
        t.display = bool(self.queue.items)
        if self.queue.items:
            t.move_cursor(row=min(keep, len(self.queue.items) - 1))
        cur = self.runner.current
        self.main("#book_bar", ProgressBar).update(progress=100 * (cur.progress if cur else 0))
        items = self.queue.items
        overall = sum((1.0 if i.status in (q.DONE, q.FAILED, q.CANCELLED) else i.progress) for i in items)
        self.main("#all_bar", ProgressBar).update(progress=100 * overall / max(1, len(items)))
        self._detail()
        self.main("#info", Static).update(self._info_text())
        cur = self.runner.current
        status = (f"Narrating {cur.name}: {cur.message}" if cur else
                  "Ready. Type /start to run the queue." if self.queue.next_waiting() else "Ready.")
        self.main("#status", Static).update(status[:200])

    def selected(self) -> q.Item | None:
        t = self.main("#queue", DataTable)
        if not self.queue.items or t.cursor_row is None or t.cursor_row >= len(self.queue.items):
            return None
        return self.queue.items[t.cursor_row]

    def _detail(self) -> None:
        it = self.selected()
        if not it:
            self.main("#detail", Static).update("")
            return
        lines = [f"[b]{it.name}[/b]   {Path(it.source).name}", it.message]
        if it.chapters:
            lines.append(f"{it.chapters} chapters, {it.words:,} words")
        if it.status == q.DONE:
            lines.append(f"Saved: {it.file}" + (f"   QC report: {Path(it.report).name}" if it.report else ""))
        if it.status == q.FAILED:
            lines.append(f"[red]{it.error}[/red]")
            if it.error_report:
                lines.append(f"Error report: {it.error_report}")
        if it.status == q.REVIEW:
            lines.append("[b]Press Return (or type /review) to listen and choose what to do.[/b]")
        if it.status == q.CANCELLED:
            lines.append("Type /start to resume it from where it stopped.")
        self.main("#detail", Static).update("\n".join(x for x in lines if x))

    @on(DataTable.RowHighlighted, "#queue")
    def _highlight(self) -> None:
        self._detail()

    # ------------------------------------------------------------------ runner events
    def _event(self, item: q.Item, ev: dict) -> None:
        def ui():
            self.refresh_queue()
            t = ev.get("type")
            if t == "review":
                self.notify(f"{item.name}: the first chapter is ready. Select it and press Return, or type /review.",
                            timeout=10)
            elif t == "done":
                self.notify(f"{item.name} is ready.", timeout=8)
            elif t == "error":
                self.notify(f"{item.name} failed. See the details below.", severity="error", timeout=10)
        self.call_from_thread(ui)

    # ------------------------------------------------------------------ command line
    @on(Input.Submitted, "#cmd")
    def _command(self, ev) -> None:
        text = ev.value.strip()
        ev.input.value = ""
        if not text:
            self.action_open()
            return
        if not is_command(text):
            from .pickers import is_book, parse_paths
            books = [p for p in parse_paths(text) if is_book(p)]
            if books:
                self.action_add(books)
            else:
                self.notify("Not a command or a book file. Type /help to see the commands.", severity="warning")
            return
        word, _, rest = text.partition(" ")
        word = ALIASES.get(word.lower(), word.lower())
        actions = {"/add": lambda: self._add_cmd(rest), "/start": self.action_start, "/cancel": self.action_cancel,
                   "/review": self.action_open, "/open": self.action_open, "/remove": self.action_remove,
                   "/new": self.action_new, "/voices": self.action_voices, "/pronounce": self.action_pronounce,
                   "/settings": self.action_settings, "/errors": self.action_errors, "/help": self.action_help,
                   "/quit": self.action_quit_app, "/up": self.action_up, "/down": self.action_down}
        if word in actions:
            actions[word]()
        else:
            self.notify(f"Unknown command {word}. Type /help to see the commands.", severity="warning")

    def _add_cmd(self, rest: str) -> None:
        from .pickers import is_book, parse_paths
        books = [p for p in parse_paths(rest) if is_book(p)] if rest.strip() else None
        self.action_add(books)

    @on(Input.Changed, "#cmd")
    def _dropped_on_cmd(self, ev) -> None:
        """A book dragged onto the command line opens Add books with it listed."""
        from .pickers import is_book, parse_paths
        if not ev.value.strip() or is_command(ev.value):
            return
        paths = parse_paths(ev.value)
        if paths and all(is_book(p) for p in paths):
            ev.input.value = ""
            self.action_add(paths)

    def on_key(self, event) -> None:
        """Up and down choose a book even while the command line has the cursor."""
        if len(self.screen_stack) > 1:
            return
        t = self.main("#queue", DataTable)
        if event.key == "up":
            t.action_cursor_up()
            event.stop()
        elif event.key == "down":
            t.action_cursor_down()
            event.stop()

    def action_help(self) -> None:
        self.push_screen(screens.HelpDialog(COMMANDS))

    def action_new(self) -> None:
        """Clear finished books from the list. Their audiobooks, and everything waiting, stay."""
        gone = [i for i in self.queue.items if i.status in (q.DONE, q.FAILED, q.CANCELLED)]
        if not gone:
            self.notify("Nothing to clear: no finished books on the list.")
            return
        unfinished = [i for i in gone if i.status != q.DONE]

        def go(yes):
            if not yes:
                return
            ids = {i.id for i in gone}
            self.queue.items = [i for i in self.queue.items if i.id not in ids]
            self.queue.save()
            self.refresh_queue()
            self.notify(f"New session: {len(gone)} book(s) cleared from the list. Their audiobooks are where "
                        "they were saved.")
        if unfinished:
            self.push_screen(screens.Confirm(
                f"Clear {len(gone)} book(s) from the list, including {len(unfinished)} that failed or were stopped? "
                "Their audiobooks and anything narrated so far stay on disk; add them again to finish them.",
                "Clear them", "Keep them"), go)
        else:
            go(True)

    # ------------------------------------------------------------------ actions
    def on_paste(self, event) -> None:
        """Files dragged from Finder onto the window arrive as a paste of their paths."""
        from .pickers import is_book, parse_paths
        if len(self.screen_stack) > 1:
            return  # a dialog is open; it handles its own pastes
        books = [p for p in parse_paths(event.text) if is_book(p)]
        if books:
            event.stop()
            self.action_add(books)

    def action_add(self, files: list[str] | None = None) -> None:
        def added(res):
            if not res:
                return
            for f in res["files"]:
                self.queue.add(f, res["voice"], res["out"], res["review"])
            self.refresh_queue()
            self.action_start()
        self.push_screen(screens.AddBooks(self.st, files), lambda res: (added(res), self.main("#cmd").focus()))

    def action_start(self) -> None:
        for it in self.queue.items:
            if it.status == q.CANCELLED:
                it.status, it.message = q.WAITING, "Will resume where it stopped"
        self.queue.save()
        if self.queue.next_waiting():
            self.runner.start()
        self.refresh_queue()

    def action_cancel(self) -> None:
        if not self.runner.running:
            return

        def go(yes):
            if yes:
                self.runner.cancel_current()
        cur = self.runner.current
        self.push_screen(screens.Confirm(f"Stop \"{cur.name if cur else 'this book'}\"? Everything narrated so far "
                                         "is kept, and it resumes from there when you start it again.",
                                         "Stop it", "Keep going"), go)

    def action_remove(self) -> None:
        it = self.selected()
        if not it or (self.runner.current and self.runner.current.id == it.id):
            return
        self.queue.remove(it.id)
        self.refresh_queue()

    def action_up(self) -> None:
        if it := self.selected():
            self.queue.move(it.id, -1)
            self.main("#queue", DataTable).move_cursor(row=max(0, self.queue.items.index(it)))
            self.refresh_queue()

    def action_down(self) -> None:
        if it := self.selected():
            self.queue.move(it.id, 1)
            self.main("#queue", DataTable).move_cursor(row=self.queue.items.index(it))
            self.refresh_queue()

    def action_open(self) -> None:
        it = self.selected()
        if not it:
            return
        if it.status == q.REVIEW:
            self.push_screen(screens.Review(it), lambda res: self._review_done(it, res))
        elif it.status == q.DONE and it.file:
            import subprocess
            subprocess.Popen(["open", "-R", it.file])

    def _review_done(self, it: q.Item, res: dict | None) -> None:
        if not res:
            return
        a = res["action"]
        if a == "continue":
            it.reviewed = True
        elif a == "voice":
            it.voice = res["voice"]
        elif a == "note":
            it.note = res["note"]
        elif a == "cancel":
            it.status, it.message = q.CANCELLED, "Cancelled at the review stop"
            self.queue.save()
            self.refresh_queue()
            return
        elif a == "pronounce":
            def after(saved):
                if saved:
                    self._requeue(it, "Redoing the first chapter with the new pronunciation")
            self.push_screen(screens.Pronounce(self.st, it.voice), after)
            return
        self._requeue(it, {"continue": "Continuing with the rest of the book",
                           "voice": f"Redoing the first chapter in {it.voice}",
                           "note": "Sending your note to the AI, then redoing the first chapter"}[a])

    def _requeue(self, it: q.Item, message: str) -> None:
        it.status, it.message = q.WAITING, message
        self.queue.save()
        self.runner.start()
        self.refresh_queue()

    def action_voices(self) -> None:
        self.push_screen(screens.Voices(self.st), lambda _: self._reload_settings())

    def action_pronounce(self) -> None:
        it = self.selected()
        self.push_screen(screens.Pronounce(self.st, it.voice if it else None))

    def action_settings(self) -> None:
        self.push_screen(screens.SettingsDialog(self.st), lambda _: self._reload_settings())

    def action_errors(self) -> None:
        self.push_screen(screens.ErrorsDialog())

    def action_quit_app(self) -> None:
        if not self.runner.running:
            self.exit()
            return

        def go(yes):
            if yes:
                self.runner.stop()
                self.exit()
        self.push_screen(screens.Confirm("A book is being worked on. Quit anyway? It stops now and resumes from "
                                         "where it stopped the next time you start Fiat Vox.", "Quit", "Stay"), go)

    @on(DataTable.RowSelected, "#queue")
    def _row_selected(self) -> None:
        self.action_open()


def run() -> int:
    FiatVox().run()
    return 0
