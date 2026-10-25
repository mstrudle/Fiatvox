"""Content-addressed cache of narrated clips, so a cancelled or crashed book resumes where it stopped.

The key covers everything that changes the sound: the exact text sent to Kokoro (after normalisation and
pronunciation overrides), the voice, the Kokoro version and the model revision. Changing a voice or adding a
pronunciation fix re-renders only the clips whose key changed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .. import paths
from .kokoro_engine import KOKORO_VERSION, REVISION, SAMPLE_RATE


def key(text: str, voice: str) -> str:
    h = hashlib.sha256()
    for part in (text, voice, KOKORO_VERSION, REVISION, "v1"):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def path_for(k: str) -> Path:
    return paths.audio_cache_dir() / k[:2] / f"{k}.flac"


def get(k: str) -> np.ndarray | None:
    p = path_for(k)
    if not p.exists():
        return None
    import soundfile as sf
    try:
        data, sr = sf.read(str(p), dtype="float32")
    except Exception:
        p.unlink(missing_ok=True)  # unreadable: it will be recorded again
        return None
    return data if sr == SAMPLE_RATE else None


def frames(k: str) -> int | None:
    """Length of a cached clip in samples, or None (and the clip removed) if it cannot be read."""
    import soundfile as sf
    p = path_for(k)
    try:
        return sf.info(str(p)).frames
    except Exception:
        p.unlink(missing_ok=True)
        return None


def put(k: str, audio: np.ndarray) -> None:
    import soundfile as sf
    import os
    p = path_for(k)
    p.parent.mkdir(parents=True, exist_ok=True)
    if audio is None or len(audio) == 0:
        audio = np.zeros(int(0.1 * SAMPLE_RATE), dtype=np.float32)  # an empty FLAC cannot be read back
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp.flac")
    sf.write(str(tmp), np.clip(audio, -1.0, 1.0), SAMPLE_RATE, subtype="PCM_16", format="FLAC")
    tmp.replace(p)


def has(k: str) -> bool:
    p = path_for(k)
    return p.exists() and p.stat().st_size > 0


def size_bytes() -> int:
    return sum(f.stat().st_size for f in paths.audio_cache_dir().rglob("*.flac"))
