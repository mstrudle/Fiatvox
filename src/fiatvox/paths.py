"""Where Fiat Vox keeps things.

Runtime (uv, Python, venv, model, voices, ffmpeg, Node, Pi): ~/.fiatvox (no spaces, so script shebangs work).
Data (work, cache, queue, settings, samples, user pronunciation lists, logs): ~/Library/Application Support/Fiat Vox.
Both can be moved with FIATVOX_HOME and FIATVOX_DATA, which the tests use.
"""
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def runtime_dir() -> Path:
    return Path(os.environ.get("FIATVOX_HOME") or Path.home() / ".fiatvox")


def data_dir() -> Path:
    return Path(os.environ.get("FIATVOX_DATA") or Path.home() / "Library" / "Application Support" / "Fiat Vox")


def ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def books_dir() -> Path:
    return ensure(data_dir() / "books")


def book_dir(book_id: str) -> Path:
    return ensure(books_dir() / book_id)


def audio_cache_dir() -> Path:
    return ensure(data_dir() / "cache" / "audio")


def samples_dir() -> Path:
    return ensure(data_dir() / "samples")


def user_pronunciation_dir() -> Path:
    return ensure(data_dir() / "pronunciation")


def logs_dir() -> Path:
    return ensure(data_dir() / "logs")


def shipped_pronunciation_dir() -> Path:
    """Shipped lists: inside the installed package, or the repository's pronunciation/ folder in development."""
    packaged = PACKAGE_DIR / "data" / "pronunciation"
    if packaged.is_dir():
        return packaged
    return PACKAGE_DIR.parent.parent / "pronunciation"


def model_dir() -> Path:
    """Kokoro model and voices: the installed copy, else the Hugging Face cache (development)."""
    return runtime_dir() / "models" / "kokoro"


def ffmpeg_path() -> str:
    """The ffmpeg Fiat Vox installed; in development, the imageio-ffmpeg binary or ffmpeg on PATH."""
    own = runtime_dir() / "bin" / "ffmpeg"
    if own.exists():
        return str(own)
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    import shutil
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg not found. Run `fiatvox doctor` to repair the install.")
    return found


def ffprobe_path() -> str | None:
    own = runtime_dir() / "bin" / "ffprobe"
    if own.exists():
        return str(own)
    import shutil
    return shutil.which("ffprobe")
