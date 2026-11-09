#!/usr/bin/env bash
# Spec2Ship installer — copies skills/ and agents/ into an agent root.
# Usage: ./install.sh [target-root]   (default: ~/.claude)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-$HOME/.claude}"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "Spec2Ship → $DEST"

mkdir -p "$DEST/skills" "$DEST/agents"

backup_if_exists() {
  local target="$1"
  if [ -e "$target" ]; then
    local bak="${target}.bak-${STAMP}"
    mv "$target" "$bak"
    echo "  备份: $target → $bak"
  fi
}

for d in "$SRC"/skills/*/; do
  name="$(basename "$d")"
  backup_if_exists "$DEST/skills/$name"
  cp -R "$d" "$DEST/skills/$name"
  echo "  skill: $name"
done

for f in "$SRC"/agents/*.md; do
  [ -e "$f" ] || continue
  name="$(basename "$f")"
  backup_if_exists "$DEST/agents/$name"
  cp "$f" "$DEST/agents/$name"
  echo "  agent: $name"
done

echo
echo "完成。下一步："
echo "  python \"$DEST/skills/spec-to-ship/scripts/check_deps.py\" \"$DEST\""
