"""The Mac's own file and folder pickers (through osascript), and paths typed or dragged into the terminal."""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

BOOK_TYPES = ["epub", "pdf", "rtf", "rtfd", "docx", "doc", "odt", "txt", "html", "htm"]


def _osa(script: str) -> str | None:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return None  # cancelled
    return r.stdout.strip()


def choose_books() -> list[str]:
    types = ", ".join(f'"{t}"' for t in BOOK_TYPES)
    script = (f'set fs to choose file with prompt "Choose books for Fiat Vox" of type {{{types}}} '
              'with multiple selections allowed\n'
              'set out to ""\nrepeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\nreturn out')
    out = _osa(script)
    return [p for p in (out or "").splitlines() if p.strip()]


def choose_folder(prompt: str = "Choose where to save the audiobooks") -> str | None:
    out = _osa(f'POSIX path of (choose folder with prompt "{prompt}")')
    return out.rstrip("/") if out else None


def parse_paths(text: str) -> list[str]:
    """Paths typed or dragged into the terminal: quoted, or with spaces escaped by backslashes, one or several."""
    text = text.strip()
    if not text:
        return []
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = [text]
    out = []
    for p in parts:
        p = p.strip()
        if p.startswith("file://"):
            from urllib.parse import unquote
            p = unquote(p[7:])
        out.append(str(Path(p).expanduser()))
    return out


def is_book(path: str) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix.lower().lstrip(".") in BOOK_TYPES
