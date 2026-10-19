"""The book model every stage reads and writes: sections of plain-text paragraphs, plus metadata.

Text here is the book's own text after cleanup. Narration makes a separate spoken copy (numerals as words,
pronunciation overrides) and never writes it back, so chapter names shown in Books stay as written.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Section:
    title: str                      # chapter name shown in Books and spoken at the start
    paragraphs: list[str] = field(default_factory=list)
    source: str = ""                # EPUB file name or "p12" for a PDF page, for reports
    label: str = ""                 # original table-of-contents label or heading, before renaming
    heading: str = ""               # the printed heading at the top of the section, removed from paragraphs
    lead: int = 0                   # paragraphs that came before the printed heading (stray labels live here)
    is_nav: bool = False            # the EPUB's own table-of-contents page

    @property
    def words(self) -> int:
        return sum(len(p.split()) for p in self.paragraphs)

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


@dataclass
class Book:
    title: str = ""
    author: str = ""
    sections: list[Section] = field(default_factory=list)
    cover: str | None = None        # path to a cover image in the work folder
    source: str = ""
    kind: str = ""                  # "epub" or "pdf"

    @property
    def words(self) -> int:
        return sum(s.words for s in self.sections)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Book":
        secs = [Section(**s) for s in d.get("sections", [])]
        return cls(**{**d, "sections": secs})

    def save(self, path: Path) -> None:
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Book":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def copy(self) -> "Book":
        return Book.from_json(json.loads(json.dumps(self.to_json())))
