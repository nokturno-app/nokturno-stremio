#!/usr/bin/env bash
# Samostatný program (PyInstaller) pro tuto platformu:  baleni/sestav.sh <platforma>
# Výsledek: dist/nokturno-<verze>-<platforma>[.exe]
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUTF8=1   # Windows jinak čte zdrojáky v cp1252
PLAT="$1"
VERZE=$(python3 -c "import re;print(re.search(r'^VERZE = \"([^\"]+)\"', open('nokturno/routes.py').read(), re.M).group(1))" 2>/dev/null \
        || python -c "import re;print(re.search(r'^VERZE = \"([^\"]+)\"', open('nokturno/routes.py').read(), re.M).group(1))")
PY=$(command -v python3 || command -v python)
rm -rf build/app && mkdir -p build/app
cp -r nokturno build/app/
find build/app -name __pycache__ -prune -exec rm -rf {} +
echo "$VERZE" > build/app/version.txt
# shellcheck disable=SC2046
EXTRA=""; case "$PLAT" in windows*) EXTRA="--noconsole --hidden-import=pystray._win32";; esac
"$PY" -m PyInstaller --noconfirm --onefile --clean $EXTRA --name "nokturno-$VERZE-$PLAT" \
  --add-data "build/app:app" $("$PY" baleni/stdlib.py) baleni/zavadec.py
