#!/usr/bin/env bash
# Aether Unified Installer — public bootstrap
# Descarga el instalador real a /tmp y lo ejecuta.
set -euo pipefail

BRANCH="${AETHER_BRANCH:-main}"
CORE_URL="${AETHER_INSTALL_CORE_URL:-https://raw.githubusercontent.com/Thomi33/AetherAI/${BRANCH}/installer/install-core.sh}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aether-installer.XXXXXX")"
CORE="$TMP_DIR/install-core.sh"
trap 'rm -rf "$TMP_DIR"' EXIT

fetch_core() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --retry-delay 1 "$CORE_URL" -o "$CORE"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$CORE" "$CORE_URL"
  else
    echo "❌ Necesito curl o wget para descargar el instalador de Aether." >&2
    exit 1
  fi
}

echo "📥 Descargando Aether Installer Core..."
echo "🌿 Branch: $BRANCH"
echo "🔗 $CORE_URL"
fetch_core

[ -s "$CORE" ] || { echo "❌ El instalador descargado está vacío." >&2; exit 1; }
chmod +x "$CORE"
exec "$CORE" "$@"
