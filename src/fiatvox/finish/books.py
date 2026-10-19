"""Import into Apple Books, and post a macOS notification."""
from __future__ import annotations

import subprocess
from pathlib import Path


def import_to_books(path: Path) -> tuple[bool, str]:
    r = subprocess.run(["open", "-a", "Books", str(path)], capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr or "").strip()


def _osa_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, message: str) -> None:
    script = f"display notification {_osa_quote(message)} with title {_osa_quote(title)}"
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)
