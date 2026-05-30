#!/usr/bin/env bash
# Instala a skill autopilot em ~/.claude/skills/autopilot/
# Uso: ./install.sh   (ou: bash install.sh)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/autopilot"

mkdir -p "$DEST/scripts"
cp "$SRC/SKILL.md" "$DEST/SKILL.md"
cp "$SRC/scripts/autopilot.py" "$DEST/scripts/autopilot.py"
chmod +x "$DEST/scripts/autopilot.py"

echo "autopilot instalado em: $DEST"
echo "No Claude Code, rode:  /autopilot setup"
