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
        p.unlink(missing_ok=True)  # a half-written clip from a crash
        return None
    return data if sr == SAMPLE_RATE else None


def put(k: str, audio: np.ndarray) -> None:
    import soundfile as sf
    p = path_for(k)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.flac")
    sf.write(str(tmp), np.clip(audio, -1.0, 1.0), SAMPLE_RATE, subtype="PCM_16", format="FLAC")
    tmp.replace(p)


def has(k: str) -> bool:
    return path_for(k).exists()


def size_bytes() -> int:
    return sum(f.stat().st_size for f in paths.audio_cache_dir().rglob("*.flac"))
