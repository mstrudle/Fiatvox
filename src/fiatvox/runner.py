"""Runs the queue in the background: one worker process per book, its JSON-line events turned into queue updates.

Narration runs in a separate process so the screen never freezes and cancel is one signal. Cancelling keeps
everything narrated so far; the book resumes from the cache when started again.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
from collections.abc import Callable

from . import paths
from . import bookqueue as q

Listener = Callable[[q.Item, dict], None]


def worker_command(job_path: str) -> list[str]:
    return [sys.executable, "-m", "fiatvox.cli", "worker", job_path]


class Runner:
    """Takes the next waiting book, runs it, records each event in the queue, repeats until none is waiting."""

    def __init__(self, queue: q.Queue, listener: Listener):
        self.queue = queue
        self.listener = listener
        self.proc: subprocess.Popen | None = None
        self.current: q.Item | None = None
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.stopping = False

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.stopping = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def cancel_current(self) -> None:
        """Stop the book in progress; what has been narrated is kept."""
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.send_signal(signal.SIGTERM)

    def stop(self) -> None:
        """Stop after cancelling the current book (used when the app closes)."""
        self.stopping = True
        self.cancel_current()

    def _loop(self) -> None:
        while not self.stopping:
            item = self.queue.next_waiting()
            if item is None:
                break
            self._run_one(item)

    def _run_one(self, item: q.Item) -> None:
        self.current = item
        item.status, item.progress, item.message, item.error = q.PREPARING, 0.0, "Starting", ""
        self.queue.save()
        self.listener(item, {"type": "status"})
        job_path = paths.ensure(paths.data_dir() / "jobs") / f"{item.id}.json"
        job_path.write_text(json.dumps(item.job().to_json()), encoding="utf-8")
        with self.lock:
            self.proc = subprocess.Popen(worker_command(str(job_path)), stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, bufsize=1)
        errlog = (paths.logs_dir() / f"{item.id}.stderr.log").open("w", encoding="utf-8")
        threading.Thread(target=lambda: [errlog.write(line) for line in self.proc.stderr], daemon=True).start()
        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            self._apply(item, ev)
            self.queue.save()
            self.listener(item, ev)
        code = self.proc.wait()
        errlog.close()
        if item.status in q.ACTIVE:  # the worker ended without a final event
            if code == 130 or self.stopping:
                item.status, item.message = q.CANCELLED, "Stopped; narration so far is kept"
            else:
                item.status = q.FAILED
                item.error = item.error or f"The worker stopped unexpectedly (code {code}); see the log."
        self.queue.save()
        self.listener(item, {"type": "status"})
        self.current = None
        with self.lock:
            self.proc = None

    @staticmethod
    def _apply(item: q.Item, ev: dict) -> None:
        t = ev.get("type")
        if t == "stage":
            item.status = q.PREPARING
            item.progress = 0.25 * float(ev.get("frac") or 0)
            item.message = f"{ev.get('stage')}: {ev.get('msg')}"
        elif t == "prepared":
            item.title = ev.get("title") or item.title
            item.chapters, item.words = ev.get("chapters", 0), ev.get("words", 0)
            item.message = f"Prepared {item.chapters} chapters"
        elif t == "narrating":
            item.status = q.NARRATING
            frac = ev["done"] / max(1, ev["total"])
            item.progress = 0.25 + 0.72 * frac
            item.eta = ev.get("eta")
            item.message = f"Chapter {ev['chapter']} of {ev['chapters']}: {ev.get('title', '')}"
        elif t == "assembling":
            item.message = "Assembling the audiobook"
            if ev.get("total"):
                item.progress = 0.97 + 0.03 * ev["done"] / max(1, ev["total"])
        elif t == "log":
            item.message = ev.get("msg", "")[:200]
        elif t == "review":
            item.status, item.progress = q.REVIEW, 0.3
            item.preview, item.review_chapter = ev["preview"], ev["chapter"]
            item.message = f"Listen to \"{ev['chapter']}\" and choose what to do"
        elif t == "done":
            item.status, item.progress, item.eta = q.DONE, 1.0, None
            item.file, item.report = ev["file"], ev.get("report", "")
            item.message = "In Apple Books" if ev.get("imported") else "Finished"
        elif t == "error":
            item.status, item.error = q.FAILED, ev.get("msg", "")
            item.error_report = ev.get("report", "")
            item.message = "Failed"
        elif t == "cancelled":
            item.status, item.message = q.CANCELLED, "Stopped; narration so far is kept"
