"""Kokoro, called directly. One model, one pipeline per accent, on the Apple GPU (MPS) when available."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np

from .. import paths

REPO_ID = "hexgrad/Kokoro-82M"
REVISION = "f3ff3571791e39611d31c381e3a41a3af07b4987"
MODEL_FILE = "kokoro-v1_0.pth"
SAMPLE_RATE = 24000
KOKORO_VERSION = "0.9.4"

# Kokoro's own list (VOICES.md), with its published quality grades. The Studio's list lacked af_nova.
VOICES = {
    "af_heart": ("American", "female", "A"), "af_alloy": ("American", "female", "C"),
    "af_aoede": ("American", "female", "C+"), "af_bella": ("American", "female", "A-"),
    "af_jessica": ("American", "female", "D"), "af_kore": ("American", "female", "C+"),
    "af_nicole": ("American", "female", "B-"), "af_nova": ("American", "female", "C"),
    "af_river": ("American", "female", "D"), "af_sarah": ("American", "female", "C+"),
    "af_sky": ("American", "female", "C-"),
    "am_adam": ("American", "male", "F+"), "am_echo": ("American", "male", "D"),
    "am_eric": ("American", "male", "D"), "am_fenrir": ("American", "male", "C+"),
    "am_liam": ("American", "male", "D"), "am_michael": ("American", "male", "C+"),
    "am_onyx": ("American", "male", "D"), "am_puck": ("American", "male", "C+"),
    "am_santa": ("American", "male", "D-"),
    "bf_alice": ("British", "female", "D"), "bf_emma": ("British", "female", "B-"),
    "bf_isabella": ("British", "female", "C"), "bf_lily": ("British", "female", "D"),
    "bm_daniel": ("British", "male", "D"), "bm_fable": ("British", "male", "C"),
    "bm_george": ("British", "male", "C"), "bm_lewis": ("British", "male", "D+"),
}
ALIASES = {v.split("_", 1)[1]: v for v in VOICES}  # "george" -> "bm_george"


def resolve_voice(name: str) -> str:
    name = name.strip().lower()
    if name in VOICES:
        return name
    if name in ALIASES:
        return ALIASES[name]
    raise ValueError(f"Unknown voice: {name}. Voices: {', '.join(VOICES)}")


def _env():
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    if (paths.model_dir() / MODEL_FILE).exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


def model_files() -> tuple[Path, Path]:
    """config.json and the model weights: Fiat Vox's installed copy, else the Hugging Face cache (downloading)."""
    own = paths.model_dir()
    if (own / MODEL_FILE).exists() and (own / "config.json").exists():
        return own / "config.json", own / MODEL_FILE
    from huggingface_hub import hf_hub_download
    return (Path(hf_hub_download(REPO_ID, "config.json", revision=REVISION)),
            Path(hf_hub_download(REPO_ID, MODEL_FILE, revision=REVISION)))


def voice_file(voice: str) -> Path:
    own = paths.model_dir() / "voices" / f"{voice}.pt"
    if own.exists():
        return own
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(REPO_ID, f"voices/{voice}.pt", revision=REVISION))


def pick_device() -> str:
    import torch
    if os.environ.get("FIATVOX_DEVICE"):
        return os.environ["FIATVOX_DEVICE"]
    return "mps" if torch.backends.mps.is_available() else "cpu"


class Engine:
    """Load once, then synthesise paragraphs. Thread-safe for one caller at a time."""

    def __init__(self, device: str | None = None):
        _env()
        import warnings
        warnings.filterwarnings("ignore", module="torch")
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)
        from kokoro import KModel
        cfg, weights = model_files()
        self.device = device or pick_device()
        self.model = KModel(repo_id=REPO_ID, config=str(cfg), model=str(weights)).to(self.device).eval()
        self._pipes = {}
        self._voices = {}
        self._lock = threading.Lock()

    def pipeline(self, accent: str):
        if accent not in self._pipes:
            from kokoro import KPipeline
            self._pipes[accent] = KPipeline(lang_code=accent, repo_id=REPO_ID, model=self.model)
        return self._pipes[accent]

    def voice_pack(self, voice: str):
        if voice not in self._voices:
            import torch
            self._voices[voice] = torch.load(voice_file(voice), weights_only=True)
        return self._voices[voice]

    def synth(self, text: str, voice: str) -> np.ndarray:
        """Audio for one paragraph or title as float32 at 24 kHz. Kokoro chunks long text itself."""
        voice = resolve_voice(voice)
        pipe = self.pipeline(voice[0])
        pack = self.voice_pack(voice)
        parts = []
        with self._lock:
            # split_pattern=None: never split the text ourselves; the paragraph has no newlines anyway
            for result in pipe(text, voice=pack, speed=1, split_pattern=None):
                if result.audio is not None:
                    parts.append(result.audio.detach().cpu().numpy().astype(np.float32))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def tokens(self, text: str, voice: str) -> list[str]:
        """The phoneme chunks Kokoro would speak, without synthesising (used to check for markup leaks)."""
        pipe = self.pipeline(resolve_voice(voice)[0])
        return [r.phonemes for r in pipe(text, voice=None, split_pattern=None, model=False)]
