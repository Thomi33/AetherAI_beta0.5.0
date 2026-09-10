#!/usr/bin/env bash
# Aether Unified Installer v3 — TUI-first wrapper
#
# The historical installer is kept in install-core.sh. The current Aether
# runtime is TUI/LangGraph, so the inactive legacy backend requirements are
# intentionally not installed by default. The future WebUI will live in a
# separate repository.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CORE="$SCRIPT_DIR/install-core.sh"

if [ ! -f "$CORE" ]; then
  echo "❌ No se encontró install-core.sh junto a install.sh." >&2
  exit 1
fi

tmp="$(mktemp "${TMPDIR:-/tmp}/aether-install.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

awk '
  /^[[:space:]]*pip_one "requirements[.]txt";[[:space:]]*pip_one "backend\/requirements[.]txt"[[:space:]]*$/ {
    print "pip_one \"requirements.txt\""
    print "# backend/ es legado/inactivo para la TUI actual; la futura WebUI va en un repo separado."
    next
  }
  { print }
' "$CORE" > "$tmp"

chmod +x "$tmp"
exec "$tmp" "$@"
