"""Voice samples: the same sentence, about five seconds, in every English voice. Made once (at install, or on
first use), stored in the data folder, and played with macOS's `afplay`."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import paths
from .narrate.kokoro_engine import SAMPLE_RATE, VOICES, resolve_voice

# One sentence for every voice, so they can be compared like for like. About five seconds when spoken.
SAMPLE_TEXT = "It was the third evening of the campaign when the general finally explained his plan."


def _version() -> str:
    return hashlib.sha1(SAMPLE_TEXT.encode()).hexdigest()[:10]


def sample_path(voice: str) -> Path:
    return paths.samples_dir() / f"{resolve_voice(voice)}.wav"


def _index() -> dict:
    p = paths.samples_dir() / "index.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def has_sample(voice: str) -> bool:
    v = resolve_voice(voice)
    return sample_path(v).exists() and _index().get(v) == _version()


def make_samples(voices: list[str] | None = None, force: bool = False,
                 progress: Callable[[int, int, str], None] | None = None) -> list[str]:
    """Create any missing samples (all English voices by default). Returns the voices made."""
    import soundfile as sf

    from .narrate.narrator import engine
    from .narrate.pronounce import accent_of  # noqa: F401  (checks the voice is English)
    todo = [resolve_voice(v) for v in (voices or list(VOICES))]
    todo = [v for v in todo if force or not has_sample(v)]
    idx = _index()
    for i, v in enumerate(todo, 1):
        if progress:
            progress(i, len(todo), v)
        audio = engine().synth(SAMPLE_TEXT, v)
        tmp = sample_path(v).with_suffix(".tmp.wav")
        sf.write(str(tmp), audio, SAMPLE_RATE, subtype="PCM_16")
        tmp.replace(sample_path(v))
        idx[v] = _version()
        (paths.samples_dir() / "index.json").write_text(json.dumps(idx, indent=1), encoding="utf-8")
    return todo


_player: subprocess.Popen | None = None


def play(voice: str, wait: bool = False) -> None:
    """Play a voice's sample, making it first if needed. A new sample stops the one already playing."""
    global _player
    v = resolve_voice(voice)
    if not has_sample(v):
        make_samples([v])
    stop()
    _player = subprocess.Popen(["afplay", str(sample_path(v))])
    if wait:
        _player.wait()


def stop() -> None:
    global _player
    if _player and _player.poll() is None:
        _player.terminate()
    _player = None


def describe(voice: str) -> str:
    accent, gender, grade = VOICES[resolve_voice(voice)]
    return f"{accent} {gender}, quality grade {grade}"
