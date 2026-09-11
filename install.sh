#!/usr/bin/env bash
# Aether Unified Installer — public entrypoint
# Toda la lógica de instalación vive en install-core.sh.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CORE="$SCRIPT_DIR/install-core.sh"

if [ ! -f "$CORE" ]; then
  echo "❌ No se encontró install-core.sh junto a install.sh." >&2
  exit 1
fi

chmod +x "$CORE"
exec "$CORE" "$@"
