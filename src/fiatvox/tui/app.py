"""Fiat Vox's terminal app: Home (the name and commands), Now (what is happening to the current book), Queue."""
from __future__ import annotations

import re
import time
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, ProgressBar, RichLog, Static, TabbedContent, TabPane

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


def hms(sec) -> str:
    if sec is None:
        return "--:--:--"
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


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
    """The name in block letters: each pixel two characters wide when there is room."""
    cell = "\u2588\u2588" if wide else "\u2588"
    blank = "  " if wide else " "
    rows = []
    for r in range(5):
        rows.append((blank).join("".join(cell if ch == "#" else blank for ch in FONT[c][r]) for c in text))
    return "\n".join(rows)


# Textual's own dark theme colours (orange accent, blue buttons), on black
ACCENT, MUTED = "#ffa62b", "#8a8a8a"

COMMANDS = [
    ("/add", "add books (or drag them here)"),
    ("/start", "start or resume the queue"),
    ("/cancel", "stop the current book"),
    ("/review", "check a paused first chapter"),
    ("/now", "what is happening now"),
    ("/queue", "the list of books"),
    ("/home", "this screen"),
    ("/new", "clear finished books"),
    ("/voices", "hear and choose voices"),
    ("/pronounce", "fix how a word is said"),
    ("/settings", "AI, pauses, folders"),
    ("/errors", "reports of failures"),
    ("/open", "show an audiobook in Finder"),
    ("/remove", "take a book off the list"),
    ("/help", "all commands and keys"),
    ("/quit", "quit; work resumes later"),
]
COMMAND_RE = re.compile(r"^/[A-Za-z?]+(?:\s|$)")  # "/add", "/new"; a path like "/Users/..." is not one


def is_command(text: str) -> bool:
    return bool(COMMAND_RE.match(text.strip()))


ALIASES = {"/a": "/add", "/s": "/start", "/c": "/cancel", "/r": "/review", "/v": "/voices", "/p": "/pronounce",
           "/t": "/settings", "/e": "/errors", "/q": "/quit", "/exit": "/quit", "/h": "/help", "/?": "/help",
           "/clear": "/new", "/n": "/now", "/list": "/queue"}
IDLE = "Nothing is being worked on. Type /add to add books, or /start to run the queue."
QUIET_STAGES = ("OCR page", "AI request")  # progress counters: shown in the telemetry, not each in the log


class FiatVox(App):
    TITLE = f"Fiat Vox {__version__}"
    SUB_TITLE = ""
    CSS = """
    Screen { background: #000000; }
    TabbedContent, TabPane, ContentSwitcher { background: #000000; }
    #tabs { height: 1fr; }
    #tabs > ContentSwitcher { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    #banner { color: $accent; content-align: center middle; width: 100%; height: auto; margin-top: 1; }
    #box { border: round $accent 60%; border-title-align: center; border-title-color: $accent; height: auto;
           margin: 0 2; padding: 0 1; }
    #info { width: 46; height: auto; padding-right: 2; border-right: solid $accent 30%; }
    #cmds { width: 1fr; height: auto; padding-left: 2; }
    #info, #cmds Static { height: auto; }
    #home_hint { margin: 1 4; color: $text-muted; width: 100%; content-align: center middle; }
    .panel { border: round $accent 60%; border-title-color: $accent; border-title-align: center;
             height: auto; padding: 0 1; margin: 0 1; }
    #now_head { height: auto; }
    #telemetry Horizontal { height: 1; }
    #telemetry Label { width: 10; }
    #telemetry ProgressBar { width: 40; }
    #telemetry .txt { width: 1fr; height: 1; }
    #lower { height: 1fr; }
    #log { width: 1fr; height: 1fr; border: round $accent 40%; border-title-color: $accent; background: #000000;
           margin: 0 0 0 1; }
    #chapters { width: 62; height: 1fr; border: round $accent 40%; border-title-color: $accent;
                background: #000000; margin: 0 1 0 1; }
    #empty { margin: 1 4; color: $text-muted; }
    #queue { height: 1fr; min-height: 4; background: #000000; margin: 1 0 0 0; overflow-x: hidden; }
    #detail { height: auto; max-height: 8; border-top: solid $accent 50%; padding: 0 1; }
    #qbars { height: 1; padding: 0 1; }
    #qbars Label { width: 11; }
    #qbars ProgressBar { width: 1fr; }
    #actions { height: auto; width: 100%; align-horizontal: center; margin: 1 2 0 2; background: #000000; }
    #actions Button { width: 18; margin: 0 2; }
    #cmd { border: tall $accent 30%; background: #000000; }
    #cmd:focus { border: tall $accent; }
    #footer { height: 1; background: #000000; }
    #status { height: 1; width: 1fr; padding: 0 1; color: $text-muted; }
    #tokens { height: 1; width: auto; padding: 0 1; color: $text-muted; }
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
        self.tele: dict = {}
        self.runs: dict = {}          # AI use per worker run in this session: {run key: usage snapshot}
        self.run_key: dict = {}       # book id -> its current run key

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        from textual.suggester import SuggestFromList
        with TabbedContent(initial="home", id="tabs"):
            with TabPane("Home", id="home"):
                yield Static(banner(), id="banner")
                with Horizontal(id="box"):
                    yield Static("", id="info")
                    with Vertical(id="cmds"):
                        yield Static(f"[b {ACCENT}]Commands[/]")
                        yield Static(self._commands_text())
                with Horizontal(id="actions"):
                    yield Button("Add books", id="main_add", variant="primary")
                    yield Button("Start", id="main_start", variant="success")
                    yield Button("Review", id="main_review", variant="warning")
                    yield Button("Cancel book", id="main_cancel", variant="error")
                yield Static("Or type a command below, or drag books from Finder onto this window.",
                             id="home_hint")
            with TabPane("Now", id="now"):
                yield Static(IDLE, id="now_head", classes="panel")
                with Vertical(id="telemetry", classes="panel"):
                    with Horizontal():
                        yield Label("Overall")
                        yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="overall_bar")
                        yield Static("", id="overall_txt", classes="txt")
                    with Horizontal():
                        yield Label("Chapter")
                        yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="chapter_bar")
                        yield Static("", id="chapter_txt", classes="txt")
                with Horizontal(id="lower"):
                    yield RichLog(id="log", wrap=True, markup=True, max_lines=2000)
                    t = DataTable(id="chapters", cursor_type="none", zebra_stripes=False)
                    t.add_columns("Ch", "Title", "Length", "Status")
                    yield t
            with TabPane("Queue", id="queue_tab"):
                yield Static("No books yet. Type /add, or drag a book from Finder onto this window.", id="empty")
                t = DataTable(id="queue", cursor_type="row", zebra_stripes=False)
                t.add_columns("Book", "Voice", "Review", "Status", "Progress", "Time left")
                yield t
                with Horizontal(id="qbars"):
                    yield Label("All books")
                    yield ProgressBar(total=100, show_eta=False, id="all_bar")
                yield Static("", id="detail")
        yield Input(placeholder="Type / for a command, or drag a book here", id="cmd",
                    suggester=SuggestFromList([c for c, _ in COMMANDS] + ["/up", "/down"], case_sensitive=False))
        with Horizontal(id="footer"):
            yield Static("", id="status")
            yield Static("", id="tokens")

    @staticmethod
    def _commands_text():
        """Commands in two columns, never wrapped."""
        from rich.table import Table
        t = Table.grid(padding=(0, 2))
        for _ in range(2):
            t.add_column(style=ACCENT, no_wrap=True, min_width=10)
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
        return "\n".join(f"[{MUTED}]{k:<11}[/]{v[:room]}" for k, v in rows)

    def on_resize(self) -> None:
        tall, wide = self.size.height >= 34, self.size.width >= 100
        self.main("#banner").display = tall
        self.main("#banner", Static).update(banner(wide=wide))
        self.main("#info").styles.width = 46 if self.size.width >= 140 else 36
        self.main("#chapters").styles.width = 62 if self.size.width >= 130 else 44

    def main(self, selector: str, kind=None):
        """A widget on the main screen, even while a dialog is open on top of it."""
        base = self.screen_stack[0]
        return base.query_one(selector, kind) if kind else base.query_one(selector)

    def on_mount(self) -> None:
        self.main("#box").border_title = f"Fiat Vox {__version__}"
        self.main("#telemetry").border_title = "Synthesis telemetry"
        self.main("#log").border_title = "Activity"
        self.main("#chapters").border_title = "Completed chapters"
        self.main("#cmd").focus()
        self.refresh_queue()
        self.set_interval(1.0, self.refresh_queue)
        if not self.st.setup_done:
            self.push_screen(screens.Setup(self.st), lambda _: self._reload_settings())

    def _reload_settings(self) -> None:
        self.st = Settings.load()

    def show_tab(self, tab: str) -> None:
        self.main("#tabs", TabbedContent).active = tab
        self.main("#cmd").focus()

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
        items = self.queue.items
        overall = sum((1.0 if i.status in (q.DONE, q.FAILED, q.CANCELLED) else i.progress) for i in items)
        self.main("#all_bar", ProgressBar).update(progress=100 * overall / max(1, len(items)))
        self._detail()
        self.main("#info", Static).update(self._info_text())
        self._telemetry()
        self.main("#main_cancel").disabled = not self.runner.running
        self.main("#main_start").disabled = self.runner.running or not (
            self.queue.next_waiting() or any(i.status == q.CANCELLED for i in self.queue.items))
        self.main("#main_review").display = any(i.status == q.REVIEW for i in self.queue.items)
        cur = self.runner.current
        status = (f"Working on {cur.name}: {cur.message}" if cur else
                  "Ready. Type /start to run the queue." if self.queue.next_waiting() else "Ready.")
        self.main("#status", Static).update(status[:200])
        self.main("#tokens", Static).update(self._counter())

    # ------------------------------------------------------------------ the token counter
    def _counter(self) -> str:
        """AI use since Fiat Vox opened, summed over every book's runs, in Feynman's notation."""
        from ..ai import get_backend
        from ..ai.base import counter_text
        total = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
        billing = ""
        for snap in self.runs.values():
            for k in total:
                total[k] += snap.get(k, 0)
            billing = snap.get("billing") or billing
        if not billing and self.st.ai_provider != "none":
            billing = get_backend(self.st.ai_provider, self.st.ai_model).usage.billing
        total["billing"] = billing
        return "AI off" if self.st.ai_provider == "none" and not total["calls"] else counter_text(total)

    def _usage_event(self, item: q.Item, ev: dict) -> None:
        typ = ev.get("type")
        if typ == "status" and item.status == q.PREPARING:
            self.run_key[item.id] = f"{item.id}:{len(self.runs)}:{time.monotonic()}"
        elif typ == "usage":
            key = self.run_key.setdefault(item.id, f"{item.id}:0")
            self.runs[key] = {k: v for k, v in ev.items() if k != "type"}

    # ------------------------------------------------------------------ the Now tab
    def _begin(self, item: q.Item) -> None:
        """A new book has started: clear the Now tab and switch to it."""
        self.tele = {"id": item.id, "name": item.name, "started": time.monotonic(), "chapters_done": 0}
        self.main("#log", RichLog).clear()
        self.main("#chapters", DataTable).clear()
        self._log(f"[b]Starting {item.name}[/b]  ({Path(item.source).name})")
        self._head(item)
        if len(self.screen_stack) == 1:
            self.show_tab("now")

    def _log(self, text: str) -> None:
        self.main("#log", RichLog).write(f"[{MUTED}]{time.strftime('%H:%M:%S')}[/]  {text}")

    def _head(self, item: q.Item, ev: dict | None = None) -> None:
        t = self.tele
        if ev:
            t.update({k: ev.get(k) for k in ("title", "author", "chapters", "words", "voice", "out_dir", "ai")})
        title = t.get("title") or item.name
        by = f" by {t['author']}" if t.get("author") else ""
        lines = [f"[b {ACCENT}]{title}[/]{by}", f"[{MUTED}]From[/]   {item.source}"]
        if t.get("out_dir"):
            lines.append(f"[{MUTED}]To[/]     {t['out_dir']}")
        facts = [f"voice {t.get('voice') or item.voice}"]
        if t.get("ai"):
            facts.append(f"AI {t['ai']}")
        if t.get("chapters"):
            facts.append(f"{t['chapters']} chapters, {t['words']:,} words")
        lines.append(f"[{MUTED}]With[/]   " + "; ".join(facts))
        self.main("#now_head", Static).update("\n".join(lines))

    def _telemetry(self) -> None:
        t = self.tele
        if not t:
            return
        elapsed = time.monotonic() - t["started"] if self.runner.current else t.get("elapsed_final")
        ob, cb = self.main("#overall_bar", ProgressBar), self.main("#chapter_bar", ProgressBar)
        if t.get("phase") == "narrating":
            pct = 100 * t["done"] / max(1, t["total"])
            ob.update(progress=pct)
            speed = f"   {t['speed']:.0f}x real time" if t.get("speed") else ""
            self.main("#overall_txt", Static).update(
                f"{pct:3.0f}%  ({t['chapters_done']}/{t['chapters_total']} chapters)   elapsed {hms(elapsed)}   "
                f"left {hms(t.get('eta'))}{speed}")
            cb.update(progress=100 * t["unit"] / max(1, t["units"]))
            self.main("#chapter_txt", Static).update(
                f"Ch {t['chapter']}: {t['ch_title'][:40]}   {t['unit']}/{t['units']} paragraphs")
        elif t.get("phase") == "preparing":
            ob.update(progress=100 * t.get("frac", 0))
            self.main("#overall_txt", Static).update(f"Preparing: {t.get('stage', '')}   elapsed {hms(elapsed)}")
            cb.update(progress=100 * t.get("frac", 0))
            self.main("#chapter_txt", Static).update(t.get("msg", "")[:70])
        elif t.get("phase") == "assembling":
            ob.update(progress=100)
            frac = t.get("asm", 0)
            cb.update(progress=100 * frac)
            self.main("#overall_txt", Static).update(f"Assembling the audiobook   elapsed {hms(elapsed)}")
            self.main("#chapter_txt", Static).update(f"{100 * frac:3.0f}% written")
        elif t.get("phase") == "finished":
            ob.update(progress=100)
            cb.update(progress=100)
            self.main("#overall_txt", Static).update(f"Finished in {hms(elapsed)}")
            self.main("#chapter_txt", Static).update("")

    def _tele_event(self, item: q.Item, ev: dict) -> None:
        t, typ = self.tele, ev.get("type")
        if typ == "status" and item.status == q.PREPARING and t.get("id") != item.id:
            self._begin(item)
            return
        if t.get("id") != item.id:
            return
        if typ == "stage":
            msg = ev.get("msg", "")
            t.update(phase="preparing", stage=ev.get("stage"), frac=float(ev.get("frac") or 0), msg=msg)
            if not msg.startswith(QUIET_STAGES) and msg != t.get("last_logged"):
                self._log(msg)
                t["last_logged"] = msg
        elif typ == "log":
            self._log(ev.get("msg", ""))
        elif typ == "prepared":
            self._head(item, ev)
            self._log(f"Prepared: {ev.get('chapters')} chapters, {ev.get('words', 0):,} words "
                      f"in {ev.get('seconds', 0)}s")
            t["chapters_total"] = ev.get("chapters")
        elif typ == "narrating":
            if t.get("phase") != "narrating":
                self._log(f"Narrating with {item.voice}")
            t.update(phase="narrating", done=ev["done"], total=ev["total"], eta=ev.get("eta"),
                     speed=ev.get("speed"), chapter=ev["chapter"], chapters_total=ev["chapters"],
                     ch_title=ev.get("title", ""), unit=ev.get("unit", 0), units=ev.get("units", 1))
        elif typ == "chapter_done":
            t["chapters_done"] = ev["chapter"]
            t["chapters_total"] = ev["chapters"]
            dur = ev.get("duration", 0)
            length = f"{int(dur // 3600)}:{int(dur % 3600 // 60):02d}:{int(dur % 60):02d}"
            self.main("#chapters", DataTable).add_row(str(ev["chapter"]), ev["title"][:34], length,
                                                     "from cache" if ev.get("from_cache") else "done")
            if not ev.get("from_cache"):
                self._log(f"Chapter {ev['chapter']} of {ev['chapters']} ready: {ev['title'][:60]} ({length})")
        elif typ == "assembling":
            if t.get("phase") != "assembling":
                self._log("Assembling the audiobook (AAC, chapters, cover)")
            t["phase"] = "assembling"
            if ev.get("total"):
                t["asm"] = ev["done"] / max(1, ev["total"])
        elif typ == "review":
            t["elapsed_final"] = time.monotonic() - t["started"]
            t["phase"] = "finished"
            self._log(f"[b]Paused for review[/b]: \"{ev.get('chapter')}\". Type /review to listen.")
        elif typ in ("done", "error", "cancelled"):
            self._clear_now()   # the book has left: what happened to it is on the Queue tab
            return
        self._telemetry()

    def _clear_now(self) -> None:
        """Empty the Now tab, and go back to Home unless another book is about to start."""
        self.tele = {}
        self.main("#log", RichLog).clear()
        self.main("#chapters", DataTable).clear()
        self.main("#now_head", Static).update(IDLE)
        for bar in ("#overall_bar", "#chapter_bar"):
            self.main(bar, ProgressBar).update(progress=0)
        for txt in ("#overall_txt", "#chapter_txt"):
            self.main(txt, Static).update("")
        if (self.main("#tabs", TabbedContent).active == "now" and len(self.screen_stack) == 1
                and not self.queue.next_waiting()):
            self.show_tab("home")

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
            self._usage_event(item, ev)
            self._tele_event(item, ev)
            self.refresh_queue()
            t = ev.get("type")
            if t == "review":
                self.notify(f"{item.name}: the first chapter is ready. Select it and press Return, or type /review.",
                            timeout=10)
            elif t == "done":
                self.notify(f"{item.name} is ready.", timeout=8)
            elif t == "error":
                self.notify(f"{item.name} failed. Details are on the Queue tab.", severity="error", timeout=10)
        self.call_from_thread(ui)

    # ------------------------------------------------------------------ command line
    @on(Input.Submitted, "#cmd")
    def _command(self, ev) -> None:
        text = ev.value.strip()
        ev.input.value = ""
        if not text:
            if self.main("#tabs", TabbedContent).active == "queue_tab":
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
                   "/quit": self.action_quit_app, "/up": self.action_up, "/down": self.action_down,
                   "/home": lambda: self.show_tab("home"), "/now": lambda: self.show_tab("now"),
                   "/queue": lambda: self.show_tab("queue_tab")}
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
        if len(self.screen_stack) > 1 or self.main("#tabs", TabbedContent).active != "queue_tab":
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
        """Quit at once and end all work, so nothing keeps running on the Mac. The book resumes next time."""
        stopped = self.runner.stop()
        self.exit(stopped)

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
        """Review the book that is waiting, whichever book is selected."""
        it = next((i for i in self.queue.items if i.status == q.REVIEW), None)
        if it:
            self.push_screen(screens.Review(it), lambda res: self._review_done(it, res))

    @on(DataTable.RowSelected, "#queue")
    def _row_selected(self) -> None:
        self.action_open()


def run() -> int:
    import signal
    app = FiatVox()

    def closed(signum, frame):  # the Terminal window was closed: end all work, then leave
        app.runner.stop()
        raise SystemExit(0)
    signal.signal(signal.SIGHUP, closed)
    signal.signal(signal.SIGTERM, closed)
    try:
        stopped = app.run()
    finally:
        app.runner.stop()
    if stopped:
        print(f"Fiat Vox has quit. \"{stopped}\" stopped and resumes from where it stopped the next time you open "
              "Fiat Vox.")
    return 0
