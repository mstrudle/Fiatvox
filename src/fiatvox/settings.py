"""User settings, saved as JSON in the data folder."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import paths


@dataclass
class Settings:
    # AI
    ai_provider: str = "pi"            # "pi", "anthropic", "openai", "gemini", "openrouter" or "none"
    ai_model: str = ""                 # Pi: "provider/model"; direct: the provider's model id ("" = default)
    review_rounds: int = 3
    setup_done: bool = False
    # narration
    default_voice: str = "bm_george"
    silence_before_title: float = 1.5
    silence_after_title: float = 0.8
    silence_paragraph: float = 0.4
    silence_chapter_end: float = 1.0
    bitrate_kbps: int = 64
    # workflow
    review_stop: bool = False          # default for new books
    qc_report: bool = True
    import_to_books: bool = True
    notify: bool = True
    output_dir: str = field(default_factory=lambda: str(Path.home() / "Audiobooks"))
    ocr_engine: str = "vision"         # "vision" or "tesseract"

    @classmethod
    def path(cls):
        return paths.data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self) -> None:
        paths.ensure(paths.data_dir())
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
        tmp.replace(self.path())

    def silences(self):
        from .narrate.chunk import Silences
        return Silences(self.silence_before_title, self.silence_after_title, self.silence_paragraph,
                        self.silence_chapter_end)
