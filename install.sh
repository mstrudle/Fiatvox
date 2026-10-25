#!/bin/bash
# Fiat Vox installer. Installs everything Fiat Vox needs into ~/.fiatvox, then puts `fiatvox` on your PATH.
#
#   curl -fsSL https://raw.githubusercontent.com/mstrudle/Fiatvox/main/install.sh | bash
#
# One question, then it runs to the end. Every download is pinned and checked against its SHA-256.
# Running it again repairs or updates the install. Nothing outside ~/.fiatvox is changed except two marked lines
# in ~/.zprofile and ~/.zshrc. It never uses Homebrew, global npm, or any Python or Node you already have.
#
# Environment (optional):
#   FIATVOX_VERSION   release to install (default: the latest release)
#   FIATVOX_SOURCE    install from this local folder instead of a release (for development and testing)
#   FIATVOX_HOME      install folder (default ~/.fiatvox)
#   FIATVOX_YES=1     do not ask; answer yes (used by `fiatvox update`)
#   FIATVOX_NO_PROFILE=1  do not edit shell profiles

set -euo pipefail

REPO="mstrudle/Fiatvox"
HOME_DIR="${FIATVOX_HOME:-$HOME/.fiatvox}"
APP="$HOME_DIR/app"
BIN="$HOME_DIR/bin"
TMP=""
STEP=0
STEPS=10

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
say() { printf '  %s\n' "$*"; }
step() { STEP=$((STEP + 1)); printf '\n\033[1m[%d/%d] %s\033[0m\n' "$STEP" "$STEPS" "$*"; }
die() { printf '\n\033[31mFiat Vox could not be installed: %s\033[0m\n' "$*" >&2; exit 1; }
cleanup() { [ -n "$TMP" ] && rm -rf "$TMP"; }
trap cleanup EXIT

# ---------------------------------------------------------------------------------------------------------------
# checks

[ "$(uname -s)" = "Darwin" ] || die "Fiat Vox runs only on macOS. This computer runs $(uname -s)."
[ "$(uname -m)" = "arm64" ] || die "Fiat Vox needs a Mac with Apple Silicon (M1 or later). This Mac is $(uname -m)."
MACOS="$(sw_vers -productVersion)"
[ "${MACOS%%.*}" -ge 13 ] || die "Fiat Vox needs macOS 13 or later. This Mac runs macOS $MACOS."
command -v curl >/dev/null || die "curl is missing."
command -v shasum >/dev/null || die "shasum is missing."

TMP="$(mktemp -d "${TMPDIR:-/tmp}/fiatvox-install.XXXXXX")"

sha_ok() {  # file expected-sha256
  [ -f "$1" ] && [ "$(shasum -a 256 "$1" | awk '{print $1}')" = "$2" ]
}

fetch() {  # url destination expected-sha256 (skips the download when the file is already right)
  local url="$1" dest="$2" sha="$3" try
  if sha_ok "$dest" "$sha"; then return 0; fi
  mkdir -p "$(dirname "$dest")"
  for try in 1 2 3; do
    if curl -fsSL --retry 2 -o "$dest.part" "$url" && sha_ok "$dest.part" "$sha"; then
      mv "$dest.part" "$dest"
      return 0
    fi
    rm -f "$dest.part"
    say "Download failed or did not match its checksum; trying again ($try of 3)..."
    sleep 2
  done
  die "could not download $url with the expected checksum."
}

# ---------------------------------------------------------------------------------------------------------------
# the one question

bold "Fiat Vox: books into audiobooks, on your Mac"
cat <<EOF

This installs, into $HOME_DIR:
  - Fiat Vox and the Python 3.12 it runs on (through uv)
  - the Kokoro speech model and its 28 English voices
  - ffmpeg (for making the audiobook files)
  - Node.js and Pi (optional sign-in route to AI models)
  - Tesseract language data (backup text recognition for scanned PDFs)

It also adds one line to ~/.zprofile and ~/.zshrc so that a new terminal finds the \`fiatvox\` command.
It needs about 3 GB of disk space and an internet connection while it runs (10 to 20 minutes).
Nothing else on your Mac is changed, and \`fiatvox uninstall\` removes it all.

EOF
if [ "${FIATVOX_YES:-}" != "1" ]; then
  if [ -r /dev/tty ]; then
    printf 'Install Fiat Vox? [y/N] '
    read -r answer < /dev/tty || answer=""
  else
    die "cannot ask for confirmation (no terminal). Run it again in Terminal, or set FIATVOX_YES=1."
  fi
  case "$answer" in y|Y|yes|YES|Yes) ;; *) echo "Nothing was installed."; exit 0 ;; esac
fi
mkdir -p "$HOME_DIR" "$BIN"

# ---------------------------------------------------------------------------------------------------------------
step "Fiat Vox itself"

if [ -n "${FIATVOX_SOURCE:-}" ]; then
  [ -f "$FIATVOX_SOURCE/pyproject.toml" ] || die "FIATVOX_SOURCE is not a Fiat Vox folder: $FIATVOX_SOURCE"
  say "From the local folder $FIATVOX_SOURCE"
  rm -rf "$APP.new"
  mkdir -p "$APP.new"
  (cd "$FIATVOX_SOURCE" && tar --exclude .venv --exclude .git --exclude scratch --exclude __pycache__ -cf - .) \
    | (cd "$APP.new" && tar -xf -)
else
  VERSION="${FIATVOX_VERSION:-}"
  if [ -z "$VERSION" ]; then
    VERSION="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" | sed 's#.*/tag/v##')"
    [ -n "$VERSION" ] && [ "$VERSION" != "https://github.com/$REPO/releases/latest" ] || die "could not find the latest release."
  fi
  say "Release $VERSION"
  REL="https://github.com/$REPO/releases/download/v$VERSION"
  curl -fsSL -o "$TMP/SHA256SUMS" "$REL/SHA256SUMS" || die "could not download the release checksums."
  SRC_SHA="$(awk '$2 == "fiatvox-'"$VERSION"'.tar.gz" {print $1}' "$TMP/SHA256SUMS")"
  [ ${#SRC_SHA} -eq 64 ] || die "the release has no checksum for fiatvox-$VERSION.tar.gz."
  fetch "$REL/fiatvox-$VERSION.tar.gz" "$TMP/fiatvox.tar.gz" "$SRC_SHA"
  rm -rf "$APP.new"
  mkdir -p "$APP.new"
  tar -xzf "$TMP/fiatvox.tar.gz" -C "$APP.new" --strip-components 1
fi
rm -rf "$APP.old"
[ -d "$APP" ] && mv "$APP" "$APP.old"
mv "$APP.new" "$APP"
rm -rf "$APP.old"

# the pinned versions and checksums travel with the source
set -a
# shellcheck disable=SC1091
. "$APP/installer/components.env"
set +a

# ---------------------------------------------------------------------------------------------------------------
step "uv $UV_VERSION (installs Python and Fiat Vox's packages)"

UV="$HOME_DIR/uv/uv"
if ! [ -x "$UV" ] || [ "$("$UV" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]; then
  fetch "$UV_URL" "$TMP/uv.tar.gz" "$UV_SHA256"
  mkdir -p "$HOME_DIR/uv"
  tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
  mv "$TMP"/uv-aarch64-apple-darwin/uv "$TMP"/uv-aarch64-apple-darwin/uvx "$HOME_DIR/uv/"
fi
say "$("$UV" --version)"

export UV_CACHE_DIR="$HOME_DIR/cache/uv"
export UV_PYTHON_INSTALL_DIR="$HOME_DIR/python"
export UV_PYTHON_BIN_DIR="$HOME_DIR/python/bin"
export UV_TOOL_DIR="$HOME_DIR/uv-tools"
export UV_TOOL_BIN_DIR="$HOME_DIR/uv-tools/bin"
export UV_PROJECT_ENVIRONMENT="$HOME_DIR/venv"
export UV_PYTHON_PREFERENCE=only-managed
export UV_NO_PROGRESS=1

# ---------------------------------------------------------------------------------------------------------------
step "Python $PYTHON_VERSION and Fiat Vox's packages (the largest step: about 1.5 GB)"

(cd "$APP" && "$UV" sync --frozen --no-dev --no-editable --python "$PYTHON_VERSION" --quiet) \
  || die "installing the Python packages failed. Run the installer again to retry."
say "$("$HOME_DIR/venv/bin/python" --version)"

# ---------------------------------------------------------------------------------------------------------------
step "ffmpeg $FFMPEG_VERSION"

install_zip_binary() {  # url sha name
  if [ -x "$BIN/$3" ] && [ -f "$BIN/.$3.sha256" ] && [ "$(cat "$BIN/.$3.sha256")" = "$2" ]; then return 0; fi
  fetch "$1" "$TMP/$3.zip" "$2"
  rm -rf "$TMP/$3"
  mkdir -p "$TMP/$3"
  unzip -q -o "$TMP/$3.zip" -d "$TMP/$3"
  local found
  found="$(find "$TMP/$3" -type f -name "$3" | head -1)"
  [ -n "$found" ] || die "$3 was not in its download."
  mv "$found" "$BIN/$3"
  chmod +x "$BIN/$3"
  echo "$2" > "$BIN/.$3.sha256"
}
install_zip_binary "$FFMPEG_URL" "$FFMPEG_SHA256" ffmpeg
install_zip_binary "$FFPROBE_URL" "$FFPROBE_SHA256" ffprobe
"$BIN/ffmpeg" -hide_banner -version | head -1 | sed 's/^/  /'

# ---------------------------------------------------------------------------------------------------------------
step "Node.js $NODE_VERSION (used only by Pi)"

NODE="$HOME_DIR/node/bin/node"
if ! [ -x "$NODE" ] || [ "$("$NODE" -v 2>/dev/null)" != "v$NODE_VERSION" ]; then
  fetch "$NODE_URL" "$TMP/node.tar.xz" "$NODE_SHA256"
  tar -xJf "$TMP/node.tar.xz" -C "$TMP"
  rm -rf "$HOME_DIR/node"
  mv "$TMP/node-v$NODE_VERSION-darwin-arm64" "$HOME_DIR/node"
fi
say "Node $("$NODE" -v)"

# ---------------------------------------------------------------------------------------------------------------
step "Pi $PI_VERSION (sign-in route to AI models)"

mkdir -p "$HOME_DIR/pi"
if ! cmp -s "$APP/installer/pi/package-lock.json" "$HOME_DIR/pi/package-lock.json" \
   || ! [ -f "$HOME_DIR/pi/node_modules/@earendil-works/pi-coding-agent/package.json" ]; then
  cp "$APP/installer/pi/package.json" "$APP/installer/pi/package-lock.json" "$HOME_DIR/pi/"
  (cd "$HOME_DIR/pi" && PATH="$HOME_DIR/node/bin:/usr/bin:/bin" NPM_CONFIG_CACHE="$HOME_DIR/cache/npm" \
     NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false \
     "$HOME_DIR/node/bin/npm" ci --omit=dev --ignore-scripts --loglevel=error) \
    || die "installing Pi failed. Run the installer again to retry."
fi
say "Pi $(PI_SKIP_VERSION_CHECK=1 PI_TELEMETRY=0 PI_CODING_AGENT_DIR="$HOME_DIR/pi-agent" \
      "$NODE" "$HOME_DIR/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js" --version)"

# ---------------------------------------------------------------------------------------------------------------
step "The Kokoro speech model and voices (about 350 MB)"

MODELS="$HOME_DIR/models/kokoro"
n=0
total="$(grep -c '^# KOKORO_FILE ' "$APP/installer/components.env")"
while read -r _ _ path sha _; do
  n=$((n + 1))
  fetch "https://huggingface.co/$KOKORO_REPO/resolve/$KOKORO_REVISION/$path" "$MODELS/$path" "$sha"
  printf '\r  %d of %d files' "$n" "$total"
done < <(grep '^# KOKORO_FILE ' "$APP/installer/components.env")
printf '\n'

# ---------------------------------------------------------------------------------------------------------------
step "Text recognition data for scanned PDFs"

fetch "$TESSDATA_URL" "$HOME_DIR/tessdata/eng.traineddata" "$TESSDATA_SHA256"
say "Tesseract English data ready (Apple's own text recognition is used first)"

# ---------------------------------------------------------------------------------------------------------------
step "The fiatvox command"

cat > "$BIN/fiatvox" <<LAUNCHER
#!/bin/bash
# Fiat Vox launcher (written by the installer).
export FIATVOX_HOME="$HOME_DIR"
export HF_HUB_OFFLINE=1
export PYTORCH_ENABLE_MPS_FALLBACK=1
export TOKENIZERS_PARALLELISM=false
export PYTHONWARNINGS=ignore
exec "$HOME_DIR/venv/bin/python" -m fiatvox.cli "\$@"
LAUNCHER
chmod +x "$BIN/fiatvox"

if [ "${FIATVOX_NO_PROFILE:-}" != "1" ]; then
  for profile in "$HOME/.zprofile" "$HOME/.zshrc"; do
    touch "$profile"
    if ! grep -q '# >>> fiatvox >>>' "$profile"; then
      printf '\n# >>> fiatvox >>>\nexport PATH="%s:$PATH"\n# <<< fiatvox <<<\n' "$BIN" >> "$profile"
      say "Added Fiat Vox to the PATH in $profile"
    fi
  done
fi

# ---------------------------------------------------------------------------------------------------------------
step "Voice samples, and a final check"

"$BIN/fiatvox" voices --make-samples || die "making the voice samples failed. Run \`fiatvox doctor\` for details."
"$BIN/fiatvox" doctor --quiet || die "the final check found a problem. Run \`fiatvox doctor\` for details."

bold "
Fiat Vox is installed.
"
echo "Open a new Terminal window and type:  fiatvox"
echo "The first time, it asks how you want to use an AI model (or none)."
