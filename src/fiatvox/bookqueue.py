"""The book queue, saved in the data folder so it survives restarts. Each book has its own voice and settings.

Only the app writes the queue; the worker reports what happens and the app records it here.
"""
from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .jobs import Job

WAITING, PREPARING, NARRATING, REVIEW, DONE, FAILED, CANCELLED = (
    "waiting", "preparing", "narrating", "review", "done", "failed", "cancelled")
ACTIVE = (PREPARING, NARRATING)
STATUS_LABEL = {WAITING: "Waiting", PREPARING: "Preparing", NARRATING: "Narrating", REVIEW: "Ready to review",
                DONE: "Done", FAILED: "Failed", CANCELLED: "Stopped"}


@dataclass
class Item:
    source: str
    voice: str
    out_dir: str
    review_stop: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str = ""                 # from the book once prepared; the file name until then
    status: str = WAITING
    progress: float = 0.0           # 0..1 for the current book
    message: str = ""
    eta: float | None = None
    chapters: int = 0
    words: int = 0
    reviewed: bool = False          # the listener approved the review chapter
    note: str = ""                  # for the AI, from the review stop
    file: str = ""
    report: str = ""
    error: str = ""
    error_report: str = ""
    preview: str = ""
    review_chapter: str = ""
    added: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))

    @property
    def name(self) -> str:
        return self.title or Path(self.source).stem

    def job(self, redecide: bool = False) -> Job:
        return Job(source=self.source, voice=self.voice, out_dir=self.out_dir,
                   review_stop=self.review_stop and not self.reviewed, note=self.note, redecide=redecide)


class Queue:
    def __init__(self, items: list[Item] | None = None):
        self.items: list[Item] = items or []

    @staticmethod
    def path() -> Path:
        return paths.data_dir() / "queue.json"

    @classmethod
    def load(cls) -> "Queue":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return cls()
        known = set(Item.__dataclass_fields__)
        items = [Item(**{k: v for k, v in d.items() if k in known}) for d in data.get("items", [])]
        q = cls(items)
        # a book that was being worked on when the app closed resumes from its cache
        for it in q.items:
            if it.status in ACTIVE:
                it.status, it.message = WAITING, "Interrupted; will resume where it stopped"
        return q

    def save(self) -> None:
        paths.ensure(paths.data_dir())
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": [asdict(i) for i in self.items]}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path())

    def add(self, source: str, voice: str, out_dir: str, review_stop: bool = False) -> Item:
        it = Item(source=str(Path(source).expanduser().resolve()), voice=voice, out_dir=out_dir,
                  review_stop=review_stop)
        self.items.append(it)
        self.save()
        return it

    def get(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def remove(self, item_id: str) -> None:
        self.items = [i for i in self.items if i.id != item_id]
        self.save()

    def move(self, item_id: str, delta: int) -> None:
        i = next((k for k, it in enumerate(self.items) if it.id == item_id), None)
        if i is None:
            return
        j = max(0, min(len(self.items) - 1, i + delta))
        self.items.insert(j, self.items.pop(i))
        self.save()

    def next_waiting(self) -> Item | None:
        return next((i for i in self.items if i.status == WAITING), None)

    def clear_finished(self) -> None:
        self.items = [i for i in self.items if i.status not in (DONE,)]
        self.save()
