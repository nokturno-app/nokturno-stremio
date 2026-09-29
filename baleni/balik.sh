#!/usr/bin/env bash
# Balík pro zavaděč: nokturno-<verze>.zip + update.json. Zip patří na adresu v update.json,
# update.json tam, kam míří update_url (VYCHOZI_UPDATE_URL v zavadec.py).
#   baleni/balik.sh <základ adresy, kde zip bude ležet> [výstupní složka]
set -euo pipefail
cd "$(dirname "$0")/.."
ZAKLAD="${1%/}"; VYSTUP="${2:-dist}"
VERZE=$(grep -oP '^VERZE = "\K[^"]+' nokturno/routes.py)
mkdir -p "$VYSTUP"; VYSTUP=$(cd "$VYSTUP" && pwd)
ZIP="$VYSTUP/nokturno-$VERZE.zip"
rm -f "$ZIP"
zip -qr -X "$ZIP" nokturno -x '*/__pycache__/*'
SHA=$(sha256sum "$ZIP" | cut -d' ' -f1)
printf '{"version": "%s", "url": "%s/nokturno-%s.zip", "sha256": "%s"}\n' "$VERZE" "$ZAKLAD" "$VERZE" "$SHA" > "$VYSTUP/update.json"
cat "$VYSTUP/update.json"
