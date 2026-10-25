"""`fiatvox doctor`, `fiatvox update` and `fiatvox uninstall`."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__, paths

REPO = "mstrudle/Fiatvox"
INSTALL_URL = f"https://raw.githubusercontent.com/{REPO}/main/install.sh"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""        # what doctor can do about it: "installer", "samples", or "" (tell the user)
    note: bool = False   # worth knowing, but not a fault in the install


def manifest() -> dict:
    """installer/components.env from the installed copy, or the repository in development."""
    for p in (paths.runtime_dir() / "app" / "installer" / "components.env",
              paths.PACKAGE_DIR.parent.parent / "installer" / "components.env"):
        if p.exists():
            out = {"KOKORO_FILES": []}
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("# KOKORO_FILE "):
                    _, _, path, sha, size = line.split()
                    out["KOKORO_FILES"].append((path, sha, int(size)))
                elif "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
            return out
    return {"KOKORO_FILES": []}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_checks(full: bool = True) -> list[Check]:
    m = manifest()
    rt = paths.runtime_dir()
    out: list[Check] = []

    def add(name, ok, detail="", fix="", note=False):
        out.append(Check(name, bool(ok), detail, fix, note))

    add("Mac with Apple Silicon", platform.system() == "Darwin" and platform.machine() == "arm64",
        f"macOS {platform.mac_ver()[0]} on {platform.machine()}")
    add("Python 3.12", sys.version_info[:2] == (3, 12), platform.python_version(), "installer")
    try:
        import kokoro  # noqa: F401
        import misaki  # noqa: F401
        import torch
        add("Python packages", True, f"torch {torch.__version__}")
        add("Apple GPU (MPS) for narration", torch.backends.mps.is_available(),
            "available" if torch.backends.mps.is_available() else "not available: narration uses the CPU (slower)")
    except Exception as e:
        add("Python packages", False, str(e)[:120], "installer")
    try:
        import en_core_web_sm  # noqa: F401
        import espeakng_loader
        add("Language data (spaCy, espeak-ng)", Path(espeakng_loader.get_data_path()).exists())
    except Exception as e:
        add("Language data (spaCy, espeak-ng)", False, str(e)[:120], "installer")

    # the Kokoro model and voices
    files = m.get("KOKORO_FILES") or []
    if files:
        bad = []
        for rel, sha, size in files:
            p = paths.model_dir() / rel
            if not p.exists() or p.stat().st_size != size or (full and rel.endswith(".pth") and sha256(p) != sha):
                bad.append(rel)
        add("Kokoro model and 28 voices", not bad,
            "all files present and checked" if not bad else f"missing or damaged: {', '.join(bad[:4])}", "installer")
    if full:
        try:
            from .narrate.g2p import markup_leaks
            add("Pronunciation engine (misaki)", markup_leaks("The [quasi](/kwˈɑːzi/) idea.", "b") is None)
        except Exception as e:
            add("Pronunciation engine (misaki)", False, str(e)[:120], "installer")

    # ffmpeg
    try:
        ff = paths.ffmpeg_path()
        enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
        add("ffmpeg", "aac" in enc, ff + (" (Apple AAC encoder)" if "aac_at" in enc else ""), "installer")
    except Exception as e:
        add("ffmpeg", False, str(e)[:120], "installer")
    add("ffprobe", bool(paths.ffprobe_path()), paths.ffprobe_path() or "missing", "installer")

    # Node and Pi
    from .ai import pi_backend
    node = pi_backend.node_bin()
    nv = subprocess.run([str(node), "-v"], capture_output=True, text=True).stdout.strip() if node.exists() else ""
    want = "v" + m.get("NODE_VERSION", "")
    add("Node.js (for Pi)", nv == want if m.get("NODE_VERSION") else bool(nv), nv or "missing", "installer")
    add("Pi", pi_backend.installed(), str(pi_backend.pi_cli()) if pi_backend.installed() else "missing", "installer")
    add("Tesseract data (backup OCR)", (rt / "tessdata" / "eng.traineddata").exists(), "", "installer")

    # samples, folders, PATH
    from . import voices
    from .narrate.kokoro_engine import VOICES
    missing = [v for v in VOICES if not voices.has_sample(v)]
    add("Voice samples", not missing, "all 28 ready" if not missing else f"{len(missing)} missing", "samples")
    try:
        d = paths.ensure(paths.data_dir())
        probe = d / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        add("Data folder", True, str(d))
    except OSError as e:
        add("Data folder", False, str(e)[:120])
    on_path = str(rt / "bin") in os.environ.get("PATH", "").split(":")
    installed = (rt / "bin" / "fiatvox").exists()
    add("fiatvox on your PATH", on_path or not installed,
        "yes" if on_path else ("open a new Terminal window (or run: source ~/.zprofile)" if installed
                               else "not installed with the installer (development copy)"), note=True)

    # the AI route
    from .ai import get_backend
    from .settings import Settings
    st = Settings.load()
    b = get_backend(st.ai_provider, st.ai_model)
    if not st.setup_done:
        add("AI", False, "not chosen yet: the first launch of `fiatvox` asks", note=True)
    elif st.ai_provider == "none":
        add("AI", True, "turned off: local rules only")
    else:
        ok, why = b.available()
        add("AI", ok, b.label if ok else why, note=True)
    from .troubleshoot import list_reports
    reps = list_reports(200)
    failed = [r for r in reps if not r["outcome"].startswith("recovered")]
    add("Error reports", True, f"{len(reps)} report(s), {len(failed)} unresolved" + ("; see `fiatvox errors`" if reps else ""))
    return out


def doctor(quiet: bool = False, repair: bool = True) -> int:
    checks = run_checks(full=not quiet)
    bad = [c for c in checks if not c.ok and not c.note]
    if not quiet or bad:
        for c in checks:
            mark = "OK  " if c.ok else ("NOTE" if c.note else "FAIL")
            print(f"  {mark}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    if not bad:
        if not quiet:
            print("\nEverything is in order.")
        return 0
    if not repair:
        return 1
    if any(c.fix == "samples" for c in bad) and not any(c.fix == "installer" for c in bad):
        from . import voices
        print("\nMaking the missing voice samples...")
        voices.make_samples()
        bad = [c for c in bad if c.fix != "samples"]
    if any(c.fix == "installer" for c in bad):
        script = paths.runtime_dir() / "app" / "install.sh"
        if script.exists():
            print("\nRepairing: running the installer again from the installed copy...")
            env = dict(os.environ, FIATVOX_YES="1", FIATVOX_SOURCE=str(paths.runtime_dir() / "app"),
                       FIATVOX_HOME=str(paths.runtime_dir()))
            return subprocess.run(["bash", str(script)], env=env).returncode
        print("\nRun the installer again to repair this:\n  curl -fsSL " + INSTALL_URL + " | bash")
        return 1
    return 1 if bad else 0


def update() -> int:
    """Run the latest release's installer, which replaces Fiat Vox and repairs anything else."""
    import tempfile
    import urllib.request
    with tempfile.TemporaryDirectory() as d:
        latest = urllib.request.urlopen(f"https://github.com/{REPO}/releases/latest", timeout=30).geturl()
        m = re.search(r"/tag/v([\w.]+)$", latest)
        if not m:
            print("Could not find the latest release.")
            return 1
        version = m.group(1)
        if version == __version__:
            print(f"Fiat Vox {__version__} is the latest version. Checking the install...")
            return doctor()
        print(f"Updating Fiat Vox {__version__} to {version}...")
        script = Path(d) / "install.sh"
        urllib.request.urlretrieve(f"https://raw.githubusercontent.com/{REPO}/v{version}/install.sh", script)
        sums = urllib.request.urlopen(f"https://github.com/{REPO}/releases/download/v{version}/SHA256SUMS",
                                      timeout=30).read().decode()
        want = next((ln.split()[0] for ln in sums.splitlines() if ln.endswith(" install.sh")), "")
        if not want or sha256(script) != want:
            print("The downloaded installer did not match the release checksum; nothing was changed.")
            return 1
        env = dict(os.environ, FIATVOX_YES="1", FIATVOX_VERSION=version)
        return subprocess.run(["bash", str(script)], env=env).returncode


def move_to_trash(path: Path) -> tuple[bool, str]:
    """macOS's own move-to-Trash (NSFileManager), which asks for no permission, unlike scripting Finder."""
    try:
        from Foundation import NSURL, NSFileManager
        ok, _, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(path)), None, None)
        return bool(ok), str(err) if err else ""
    except Exception as e:
        return False, str(e)


def uninstall(yes: bool = False) -> int:
    rt = paths.runtime_dir()
    data = paths.data_dir()
    print("This removes Fiat Vox from this Mac:")
    print(f"  - {rt}  (Fiat Vox, its Python, Kokoro model, ffmpeg, and its own copies of Node and Pi)")
    print(f"  - {data}  (queue, settings, narration cache, your pronunciation lists): moved to the Trash,")
    print("    so you can still recover your pronunciation lists from there")
    print("  - the two Fiat Vox lines in ~/.zprofile and ~/.zshrc")
    print("  - any API keys Fiat Vox saved in your Keychain")
    print("Your finished audiobooks, and the books in Apple Books, are not touched.")
    if not yes:
        try:
            ans = input("\nRemove Fiat Vox? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("Nothing was removed.")
            return 0
    from .ai import keys
    from .ai.direct import PROVIDERS
    for p in PROVIDERS:
        keys.delete(p)
    if data.exists():
        ok, why = move_to_trash(data)
        if not ok:
            print(f"Could not move {data} to the Trash ({why}); it was left in place.")
    for prof in (Path.home() / ".zprofile", Path.home() / ".zshrc"):
        if prof.exists():
            text = prof.read_text(encoding="utf-8")
            new = re.sub(r"\n?# >>> fiatvox >>>.*?# <<< fiatvox <<<\n?", "\n", text, flags=re.S)
            if new != text:
                prof.write_text(new, encoding="utf-8")
    if rt.exists():
        shutil.rmtree(rt, ignore_errors=True)
    print("Fiat Vox has been removed. Open a new Terminal window to finish.")
    return 0
