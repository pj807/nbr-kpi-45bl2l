#!/bin/bash
# Manual update from the Mac: pull fresh numbers from GHL (read-only), privacy check, publish.
#   ./refresh.sh            pull + publish
#   ./refresh.sh --ui FILE  first merge a Chrome read of the GHL email screens (UI-only numbers), then pull + publish
# The working copy in iCloud has no .git: this script publishes from a temporary clone (never inside iCloud).
set -euo pipefail
cd "$(dirname "$0")"
REPO="https://github.com/pj807/nbr-kpi-45bl2l.git"

if [ "${1:-}" = "--ui" ]; then
  echo "0/4 Merging the GHL email-screen read into ui.json…"
  python3 -I scripts/ui_update.py "$2"
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "1/4 Getting the latest published version…"
git clone -q --depth 1 "$REPO" "$TMP/site"
# keep the bot's newest data.json as the starting point (its _src carries the last API read)
cp "$TMP/site/data.json" data.json 2>/dev/null || true

echo "2/4 Pulling fresh numbers from GHL (read-only, NBR location)…"
python3 -I scripts/pull.py

echo "3/4 Privacy check…"
FILES="index.html data.json report.txt config.json ui.json README.md robots.txt .nojekyll .gitignore refresh.sh scripts/pull.py scripts/privacy_scan.py scripts/ui_update.py .github/workflows/daily-refresh.yml"
python3 -I scripts/privacy_scan.py $FILES
for f in $FILES; do mkdir -p "$TMP/site/$(dirname "$f")"; cp "$f" "$TMP/site/$f"; done

echo "4/4 Publishing…"
cd "$TMP/site"
git add -A
python3 -I scripts/privacy_scan.py            # scans exactly what is staged
if git diff --cached --quiet; then echo "No changes."; exit 0; fi
git -c user.name="nbr-kpi-mac" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -q -m "Update from Mac $(date '+%Y-%m-%d %H:%M')"
git push -q
echo "Done. The live page updates within about a minute."
