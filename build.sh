#!/usr/bin/env bash
# Собирает архив скилла для загрузки в Claude: dist/yandex-direct-audit-<версия>.zip
# Внутри архива — папка yandex-direct-audit/ с SKILL.md (так требует загрузка в Claude).
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(cat yandex-direct-audit/VERSION)
mkdir -p dist
OUT="dist/yandex-direct-audit-${VERSION}.zip"
rm -f "$OUT" dist/yandex-direct-audit.zip
zip -rq "$OUT" yandex-direct-audit -x '*/__pycache__/*' '*.pyc' '*/.env' '*/direct_audit_data/*' '*.DS_Store'
cp "$OUT" dist/yandex-direct-audit.zip
echo "$OUT"
unzip -l "$OUT"
