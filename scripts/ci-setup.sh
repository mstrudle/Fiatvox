#!/bin/bash
# Set up a GitHub Actions macOS runner for the tests: the pinned uv and ffmpeg from installer/components.env
# (checksums verified), then Fiat Vox with its locked dependencies. FIATVOX_HOME must be set.
set -euo pipefail
set -a
. installer/components.env
set +a
mkdir -p "$FIATVOX_HOME/bin" "$FIATVOX_HOME/uv"
get() {  # url sha256 destination
  curl -fsSL --retry 3 -o "$3" "$1"
  echo "$2  $3" | shasum -a 256 -c -
}
get "$UV_URL" "$UV_SHA256" /tmp/uv.tar.gz
tar -xzf /tmp/uv.tar.gz -C /tmp
mv /tmp/uv-aarch64-apple-darwin/uv "$FIATVOX_HOME/uv/uv"
for tool in ffmpeg ffprobe; do
  url_var="$(echo "$tool" | tr a-z A-Z)_URL"; sha_var="$(echo "$tool" | tr a-z A-Z)_SHA256"
  get "${!url_var}" "${!sha_var}" "/tmp/$tool.zip"
  unzip -q -o "/tmp/$tool.zip" -d "/tmp/$tool"
  mv "$(find "/tmp/$tool" -type f -name "$tool" | head -1)" "$FIATVOX_HOME/bin/$tool"
  chmod +x "$FIATVOX_HOME/bin/$tool"
done
"$FIATVOX_HOME/uv/uv" sync --frozen --python "$PYTHON_VERSION"
"$FIATVOX_HOME/bin/ffmpeg" -hide_banner -version | head -1
