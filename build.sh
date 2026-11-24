#!/usr/bin/env bash
# Собирает архив скилла для загрузки в Claude: dist/siblead-direct-audit-<версия>.zip
# Внутри архива — папка siblead-direct-audit/ с SKILL.md (так требует загрузка в Claude).
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(cat siblead-direct-audit/VERSION)
mkdir -p dist
OUT="dist/siblead-direct-audit-${VERSION}.zip"
rm -f "$OUT" dist/siblead-direct-audit.zip
zip -rq "$OUT" siblead-direct-audit -x '*/__pycache__/*' '*.pyc' '*/.env' '*/direct_audit_data/*' '*.DS_Store'
cp "$OUT" dist/siblead-direct-audit.zip
echo "$OUT"
unzip -l "$OUT"
