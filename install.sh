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

# Asegurar que el launcher global quede disponible también en futuras shells.
# No alcanza con exportarlo dentro de este proceso: el usuario debe poder
# ejecutar `aether` inmediatamente después de cerrar/reabrir su terminal.
LAUNCH_DEST="${AETHER_BIN_DIR:-$HOME/.local/bin}"
if [ "$LAUNCH_DEST" = "$HOME/.local/bin" ]; then
  case ":${PATH}:" in
    *":$LAUNCH_DEST:"*) ;;
    *)
      for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
        if [ -f "$rc" ] || [ "$rc" = "$HOME/.zshrc" ]; then
          grep -Fqx 'export PATH="$HOME/.local/bin:$PATH"' "$rc" 2>/dev/null ||
            printf '\n# Aether launcher\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
          break
        fi
      done
      export PATH="$LAUNCH_DEST:$PATH"
      ;;
  esac
fi

chmod +x "$tmp"
exec "$tmp" "$@"
