"""Join narrated clips into one chaptered .m4b with ffmpeg: AAC, FFMETADATA chapters, title, author, cover.

The whole book's PCM is streamed into a single ffmpeg process (one encode, no gaps from joining separately
encoded files). Chapter start times come from exact sample counts, so each marker falls on the first sample of
the silence before that chapter's spoken title.
"""
from __future__ import annotations

import functools
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import paths
from .kokoro_engine import SAMPLE_RATE


@dataclass
class ChapterAudio:
    title: str
    pieces: Callable[[], Iterator[np.ndarray]]   # yields float32 arrays in order, silences included
    samples: int                                  # total length, known in advance


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.float32)


def _escape(v: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", str(v))


def ffmetadata(title: str, author: str, chapters: list[tuple[str, int, int]]) -> str:
    """chapters: (title, start sample, end sample)."""
    L = [";FFMETADATA1", f"title={_escape(title)}", f"album={_escape(title)}", f"artist={_escape(author)}",
         f"album_artist={_escape(author)}", "genre=Audiobook", "media_type=2", "encoder=Fiat Vox"]
    for t, a, b in chapters:
        L += ["", "[CHAPTER]", f"TIMEBASE=1/{SAMPLE_RATE}", f"START={a}", f"END={b}", f"title={_escape(t)}"]
    return "\n".join(L) + "\n"


@functools.lru_cache(maxsize=1)
def aac_encoder() -> str:
    """Apple's AudioToolbox AAC encoder when this ffmpeg has it (better at low bitrates), else ffmpeg's own."""
    try:
        out = subprocess.run([paths.ffmpeg_path(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:
        return "aac"
    return "aac_at" if re.search(r"\baac_at\b", out) else "aac"


def safe_filename(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", s)
    return re.sub(r"\s+", " ", s).strip(" .")[:150] or "Audiobook"


def write_m4b(out: Path, title: str, author: str, chapters: Iterable[ChapterAudio], cover: str | None,
              bitrate_kbps: int = 64, progress: Callable[[int, int], None] | None = None,
              cancelled: Callable[[], bool] = lambda: False) -> list[tuple[str, int, int]]:
    """Write the audiobook atomically. Returns the chapter table (title, start sample, end sample)."""
    chapters = list(chapters)
    table, pos = [], 0
    for ch in chapters:
        table.append((ch.title, pos, pos + ch.samples))
        pos += ch.samples
    total = pos
    tmp_dir = paths.ensure(paths.data_dir() / "tmp")
    meta = tmp_dir / f"{out.stem}.ffmeta.txt"
    meta.write_text(ffmetadata(title, author, table), encoding="utf-8")
    part = out.with_name(out.name + ".part")
    cmd = [paths.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0", "-i", str(meta)]
    has_cover = bool(cover) and Path(cover).exists()
    if has_cover:
        cmd += ["-i", str(cover)]
    cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
    if has_cover:
        cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    cmd += ["-c:a", aac_encoder(), "-b:a", f"{bitrate_kbps}k", "-movflags", "+faststart", "-f", "ipod", str(part)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    written = 0
    try:
        for ch in chapters:
            for a in ch.pieces():
                if cancelled():
                    raise InterruptedError("cancelled")
                pcm = (np.clip(a, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                proc.stdin.write(pcm)
                written += len(a)
                if progress:
                    progress(written, total)
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err.strip()[-600:]}")
    except BaseException:
        proc.kill()
        part.unlink(missing_ok=True)
        raise
    finally:
        meta.unlink(missing_ok=True)
    if written != total:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"audio length mismatch: wrote {written} samples, expected {total}")
    part.replace(out)
    return table


def probe_chapters(path: Path) -> list[dict]:
    """Chapters as ffprobe or ffmpeg reports them: [{"title", "start", "end"}] in seconds."""
    import json
    probe = paths.ffprobe_path()
    if probe:
        r = subprocess.run([probe, "-v", "error", "-print_format", "json", "-show_chapters", "-show_format", str(path)],
                           capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout or "{}")
        return [{"title": c.get("tags", {}).get("title", ""), "start": float(c["start_time"]),
                 "end": float(c["end_time"])} for c in d.get("chapters", [])]
    r = subprocess.run([paths.ffmpeg_path(), "-hide_banner", "-i", str(path), "-f", "ffmetadata", "-"],
                       capture_output=True, text=True, timeout=60)
    out, cur = [], None
    tb = 1.0
    for line in r.stdout.splitlines():
        if line == "[CHAPTER]":
            cur = {}
            out.append(cur)
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            if k == "TIMEBASE":
                n, d = v.split("/")
                tb = int(n) / int(d)
            elif k in ("START", "END"):
                cur[k.lower()] = int(v) * tb
            elif k == "title":
                cur["title"] = v
    return out
