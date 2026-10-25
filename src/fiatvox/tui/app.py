"""Fiat Vox's terminal app: the queue, progress, and every action one key away."""
from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Label, ProgressBar, Static

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


class FiatVox(App):
    TITLE = f"Fiat Vox {__version__}"
    SUB_TITLE = ""
    CSS = """
    #empty { margin: 2 4; color: $text-muted; }
    #queue { height: 1fr; }
    #detail { height: auto; max-height: 12; border-top: solid $accent; padding: 0 1; }
    #detail Label { margin-right: 1; }
    #bars { height: auto; padding: 0 1; }
    #bars Horizontal { height: 1; }
    #bars Label { width: 11; }
    #bars ProgressBar { width: 1fr; }
    #actions { height: auto; padding: 0 1; }
    #actions Button { margin-right: 1; }
    """
    BINDINGS = [
        Binding("a", "add", "Add books"),
        Binding("s", "start", "Start"),
        Binding("c", "cancel", "Cancel book"),
        Binding("enter", "open", "Review / open", show=False),
        Binding("r", "remove", "Remove"),
        Binding("u", "up", "Move up", show=False),
        Binding("d", "down", "Move down", show=False),
        Binding("v", "voices", "Voices"),
        Binding("p", "pronounce", "Pronunciation"),
        Binding("t", "settings", "Settings"),
        Binding("e", "errors", "Errors"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.st = Settings.load()
        self.queue = q.Queue.load()
        self.queue.save()
        self.runner = Runner(self.queue, self._event)

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("No books yet. Press A to add books.", id="empty")
        t = DataTable(id="queue", cursor_type="row", zebra_stripes=True)
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
        with Horizontal(id="actions"):
            yield Button("Add books", id="main_add", variant="primary")
            yield Button("Start", id="main_start", variant="success")
            yield Button("Cancel book", id="main_cancel", variant="error")
            yield Button("Review", id="main_review")
        yield Footer()

    def main(self, selector: str, kind=None):
        """A widget on the main screen, even while a dialog is open on top of it."""
        base = self.screen_stack[0]
        return base.query_one(selector, kind) if kind else base.query_one(selector)

    def on_mount(self) -> None:
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
        self.main("#main_cancel").disabled = not self.runner.running
        self.main("#main_start").disabled = self.runner.running or not self.queue.next_waiting()
        sel = self.selected()
        self.main("#main_review").display = bool(sel and sel.status == q.REVIEW)

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
            lines.append("[b]Press Return to listen and choose what to do.[/b]")
        if it.status == q.CANCELLED:
            lines.append("Press S to resume it from where it stopped.")
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
                self.notify(f"{item.name}: the first chapter is ready to review. Select it and press Return.",
                            timeout=10)
            elif t == "done":
                self.notify(f"{item.name} is ready.", timeout=8)
            elif t == "error":
                self.notify(f"{item.name} failed. See the details below.", severity="error", timeout=10)
        self.call_from_thread(ui)

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
        self.push_screen(screens.AddBooks(self.st, files), added)

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

    @on(Button.Pressed, "#main_add")
    def _b_add(self) -> None:
        self.action_add()

    @on(Button.Pressed, "#main_start")
    def _b_start(self) -> None:
        self.action_start()

    @on(Button.Pressed, "#main_cancel")
    def _b_cancel(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#main_review")
    def _b_review(self) -> None:
        self.action_open()

    @on(DataTable.RowSelected, "#queue")
    def _row_selected(self) -> None:
        self.action_open()


def run() -> int:
    FiatVox().run()
    return 0
