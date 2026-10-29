#!/bin/bash
# SVG -> PNG（chromium 无头，2 倍图）
set -e
cd "$(dirname "$0")/out/charts"
for f in "$@"; do
  w=$(grep -o 'width="[0-9]*"' "$f.svg" | head -1 | grep -o '[0-9]*')
  h=$(grep -o 'height="[0-9]*"' "$f.svg" | head -1 | grep -o '[0-9]*')
  echo "<html><body style=\"margin:0\"><img src=\"$f.svg\" width=\"$w\" height=\"$h\"></body></html>" > "$f.html"
  chromium --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --user-data-dir="$(cd .. && pwd)/.chrome" --hide-scrollbars \
    --force-device-scale-factor=2 --window-size=$w,$h \
    --screenshot="$f.png" "$f.html" >/dev/null 2>&1
  echo "  $f.png $(identify -format '%wx%h' "$f.png")"
done
