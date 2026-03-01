#!/usr/bin/env bash
set -euo pipefail
FILE="${1:-}"
SRC="${2:-}"
if [[ -z "$FILE" || -z "$SRC" ]]; then
  echo "usage: ./nuke_file.sh <target_file> <backup_file>"
  exit 1
fi
cp -v "$FILE" "${FILE}.before_nuke_$(date +%Y%m%d_%H%M%S).bak"
cp -v "$SRC" "$FILE"
python3 -m py_compile "$FILE"
echo "NUKE OK ✅ compiled $FILE"
