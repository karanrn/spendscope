#!/usr/bin/env bash
# Rebuild docs/spendscope-demo.mp4 and docs/screenshots/ from made-up demo data (make media).
# Needs Python with requirements.txt installed, node/npm (Playwright and its Chromium are installed
# here on first run) and ffmpeg. On a bare Linux, Chromium may also need: npx playwright install-deps
# Work happens in build/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
build="$here/build"
PYTHON="$("${PYTHON:-python3}" -c "import sys; print(sys.executable)")"
export PYTHON

for tool in node npm ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "make media needs $tool" >&2; exit 1; }
done
if [ ! -d "$here/node_modules/playwright" ]; then
  npm ci --prefix "$here" --no-audit --no-fund
fi
# Playwright refuses to install on OS releases it doesn't list yet, though an already
# downloaded Chromium usually still runs; the recording step fails clearly if none does.
"$here/node_modules/.bin/playwright" install chromium ||
  echo "Could not install Chromium for Playwright; trying the one already downloaded." >&2

rm -rf "$build"
mkdir -p "$build"
echo "== Demo project with made-up data"
"$PYTHON" "$here/make_demo.py" "$repo" "$build/demo"
echo "== Terminal output"
"$PYTHON" "$here/capture.py" "$build/demo" "$build/terminal.json" >/dev/null
echo "== Recording the walkthrough (about 2 minutes)"
node "$here/record.mjs" "$build/demo" "$build"
ffmpeg -v error -y -i "$build/walkthrough.webm" -c:v libx264 -preset slow -crf 26 -tune stillimage \
  -pix_fmt yuv420p -r 30 -movflags +faststart -an "$build/walkthrough.mp4"
echo "== Screenshots"
node "$here/shots.mjs" "$build/demo" "$build"

cp "$build/walkthrough.mp4" "$repo/docs/spendscope-demo.mp4"
cp "$build"/screenshots/* "$repo/docs/screenshots/"
seconds="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$build/walkthrough.mp4" | awk '{printf "%d", $1 + 0.5}')"
"$PYTHON" - "$repo/README.md" "$seconds" <<'PY'
import re, sys
from pathlib import Path
readme = Path(sys.argv[1])
readme.write_text(re.sub(r"\d+-second walkthrough", f"{sys.argv[2]}-second walkthrough", readme.read_text(encoding="utf-8")), encoding="utf-8")
PY
echo "Updated docs/spendscope-demo.mp4, docs/screenshots/ and the video length in README.md (${seconds}s)."
echo "If the tour or the features changed, also check the video description in README.md."
