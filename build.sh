#!/usr/bin/env bash
# Собирает архивы из одной методики (siblead-direct-audit/references):
#   dist/siblead-direct-audit.zip          — скилл для Claude (папка с SKILL.md внутри)
#   dist/siblead-direct-audit-chatgpt.zip  — Custom GPT: instructions.md + knowledge/ + SETUP.md
#   dist/siblead-direct-audit-gemini.zip   — Gem:        instructions.md + knowledge/ + SETUP.md
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(cat siblead-direct-audit/VERSION)
rm -rf dist && mkdir -p dist
EXCL=(-x '*.DS_Store')

# Claude
zip -rq "dist/siblead-direct-audit-${VERSION}.zip" siblead-direct-audit "${EXCL[@]}"
cp "dist/siblead-direct-audit-${VERSION}.zip" dist/siblead-direct-audit.zip

# ChatGPT / Gemini
LIMIT=8000
CHARS=$(python3 -c "import sys;print(len(open('platforms/instructions.md',encoding='utf-8').read()))")
if [ "$CHARS" -gt "$LIMIT" ]; then echo "platforms/instructions.md: $CHARS символов > $LIMIT (лимит Custom GPT)"; exit 1; fi
for P in chatgpt gemini; do
  D="dist/build/siblead-direct-audit-$P"
  mkdir -p "$D/knowledge"
  cp platforms/instructions.md "$D/instructions.md"
  cp "platforms/$P/SETUP.md" "$D/SETUP.md"
  cp siblead-direct-audit/references/*.md "$D/knowledge/"
  # Gem: лимит 10 файлов знаний и нет сборки PDF из HTML — шаблон только для ChatGPT
  [ "$P" = chatgpt ] && cp siblead-direct-audit/references/report.html "$D/knowledge/"
  cp siblead-direct-audit/LICENSE siblead-direct-audit/NOTICE "$D/"
  (cd dist/build && zip -rq "../siblead-direct-audit-$P-${VERSION}.zip" "siblead-direct-audit-$P" "${EXCL[@]}")
  cp "dist/siblead-direct-audit-$P-${VERSION}.zip" "dist/siblead-direct-audit-$P.zip"
done
rm -rf dist/build
echo "instructions.md: $CHARS / $LIMIT символов"
ls -1 dist
