"""misaki grapheme-to-phoneme, the same front end Kokoro uses. Used to verify overrides and to dry-run a book."""
from __future__ import annotations

import functools
import re

MARKUP = re.compile(r"[\[\]()/]")


@functools.lru_cache(maxsize=2)
def g2p(accent: str):
    """misaki's English G2P for 'a' (American) or 'b' (British), with the bundled espeak-ng fallback."""
    from misaki import en, espeak
    british = accent == "b"
    return en.G2P(trf=False, british=british, fallback=espeak.EspeakFallback(british=british))


def phonemes(text: str, accent: str) -> str:
    ps, _ = g2p(accent)(text)
    return ps


def markup_leaks(text: str, accent: str) -> str | None:
    """The phonemes if override markup survived into them (Kokoro would read it aloud), else None."""
    ps = phonemes(text, accent)
    return ps if MARKUP.search(ps) else None


@functools.lru_cache(maxsize=1)
def kokoro_vocab() -> frozenset[str]:
    """Every phoneme symbol the Kokoro model knows."""
    import json

    from .. import paths
    cfg = paths.model_dir() / "config.json"
    if not cfg.exists():
        from huggingface_hub import hf_hub_download
        from .kokoro_engine import REPO_ID, REVISION
        cfg = hf_hub_download(REPO_ID, "config.json", revision=REVISION)
    return frozenset(json.loads(open(cfg, encoding="utf-8").read())["vocab"])


def unknown_symbols(ps: str) -> set[str]:
    return {c for c in ps if c not in kokoro_vocab() and not c.isspace()}
