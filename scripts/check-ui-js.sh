#!/usr/bin/env bash
# Syntax-check the inline JavaScript of the bundled browser UI.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="$(mktemp --suffix=.js)"
trap 'rm -f "$OUT"' EXIT

python3 - "$OUT" <<'PY'
import re
import sys
from pathlib import Path

html = Path("src/aphrodite/api/static/index.html").read_text(encoding="utf-8")
match = re.search(r"<script>(.*?)</script>", html, re.S)
if not match:
    sys.exit("No inline <script> found in index.html")
Path(sys.argv[1]).write_text(match.group(1), encoding="utf-8")
PY

node --check "$OUT"
echo "UI JavaScript syntax OK"
