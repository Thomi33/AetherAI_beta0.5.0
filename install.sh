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

# ---------------------------------------------------------------------------
# SearXNG bootstrap
# ---------------------------------------------------------------------------
# Aether uses SearXNG as its local web-search backend. Keep it local-only,
# install Docker when needed, and let the user choose the published port.
# AETHER_SEARXNG_PORT overrides the prompt (useful for automation/CI).
bootstrap_searxng() {
  local port="${AETHER_SEARXNG_PORT:-}"
  local input=""
  local sudo_cmd=""
  local compose_cmd=""
  local os_id="unknown"
  local searx_dir="$HOME/.local/share/aether/searxng"
  local env_file="$searx_dir/.env"
  local compose_file="$searx_dir/docker-compose.yml"
  local searx_url=""

  [ "$(id -u)" = "0" ] || sudo_cmd="sudo"

  if [ -z "$port" ]; then
    # -y means non-interactive: keep the documented default.
    case " $* " in
      *" --yes "*|*" -y "*) port="8080" ;;
      *)
        read -r -p "🔎 Puerto para SearXNG [8080]: " input || true
        port="${input:-8080}"
        ;;
    esac
  fi

  if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "❌ Puerto inválido para SearXNG: $port" >&2
    exit 1
  fi

  # Detect the distro before installing anything.
  if [ -f /etc/os-release ]; then
    . /etc/os-release
    os_id="${ID:-unknown}"
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "🐳 Docker no encontrado; instalándolo..."
    case "$os_id" in
      arch|endeavouros|manjaro|cachyos)
        $sudo_cmd pacman -Sy --needed --noconfirm docker docker-compose curl
        ;;
      debian|ubuntu|linuxmint|pop)
        $sudo_cmd apt-get update -y
        $sudo_cmd apt-get install -y docker.io docker-compose-plugin curl || \
          $sudo_cmd apt-get install -y docker.io docker-compose curl
        ;;
      fedora|nobara|rhel)
        $sudo_cmd dnf install -y docker docker-compose-plugin curl || \
          $sudo_cmd dnf install -y docker docker-compose
        ;;
      *)
        echo "⚠️ Distro '$os_id' no reconocida; intentando Docker oficial..."
        command -v curl >/dev/null 2>&1 || {
          echo "❌ Necesito curl para instalar Docker en esta distro." >&2
          exit 1
        }
        curl -fsSL https://get.docker.com | $sudo_cmd sh
        ;;
    esac
  fi

  command -v docker >/dev/null 2>&1 || {
    echo "❌ Docker no quedó disponible después de la instalación." >&2
    exit 1
  }

  if docker compose version >/dev/null 2>&1; then
    compose_cmd="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    compose_cmd="docker-compose"
  else
    echo "❌ No se encontró Docker Compose (docker compose/docker-compose)." >&2
    exit 1
  fi

  # Start Docker. If the current user has no docker-group access yet, use sudo
  # for this installer run instead of silently changing group membership.
  if ! docker info >/dev/null 2>&1; then
    echo "🔧 Iniciando Docker..."
    $sudo_cmd systemctl enable --now docker 2>/dev/null || true
  fi

  # Prefer non-root Docker when available; otherwise use sudo for compose.
  local docker_prefix=""
  if ! docker info >/dev/null 2>&1; then
    docker_prefix="$sudo_cmd"
  fi
  if [ -n "$docker_prefix" ]; then
    if $docker_prefix docker info >/dev/null 2>&1; then
      compose_cmd="$docker_prefix $compose_cmd"
    else
      echo "❌ Docker está instalado pero no se puede acceder al daemon." >&2
      echo "   Probá: sudo systemctl enable --now docker" >&2
      exit 1
    fi
  fi

  # Check the selected host port before touching the stack.
  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]"; then
    echo "❌ El puerto $port ya está en uso." >&2
    echo "   Elegí otro: AETHER_SEARXNG_PORT=XXXX ./install.sh" >&2
    exit 1
  fi

  echo "🔎 SearXNG → http://127.0.0.1:$port"
  mkdir -p "$searx_dir/core-config"

  if [ ! -f "$compose_file" ]; then
    echo "📥 Descargando configuración oficial de SearXNG..."
    command -v curl >/dev/null 2>&1 || {
      echo "❌ curl es necesario para descargar la configuración de SearXNG." >&2
      exit 1
    }
    curl -fsSL \
      https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml \
      -o "$compose_file"
  fi

  if [ ! -f "$env_file" ]; then
    curl -fsSL \
      https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example \
      -o "$env_file"
  fi

  # The official compose template publishes SEARXNG_PORT on SEARXNG_HOST.
  # Bind to loopback so the local search backend is not exposed on the LAN.
  if grep -q '^SEARXNG_HOST=' "$env_file"; then
    sed -i 's/^SEARXNG_HOST=.*/SEARXNG_HOST=127.0.0.1/' "$env_file"
  else
    printf '\nSEARXNG_HOST=127.0.0.1\n' >> "$env_file"
  fi
  if grep -q '^SEARXNG_PORT=' "$env_file"; then
    sed -i "s/^SEARXNG_PORT=.*/SEARXNG_PORT=$port/" "$env_file"
  else
    printf 'SEARXNG_PORT=%s\n' "$port" >> "$env_file"
  fi

  searx_url="http://127.0.0.1:$port"

  if $compose_cmd -f "$compose_file" --env-file "$env_file" ps 2>/dev/null | grep -q 'Up'; then
    if curl -fsS --max-time 2 "$searx_url/" >/dev/null 2>&1; then
      echo "   ✔ SearXNG ya está funcionando en $searx_url"
      return 0
    fi
  fi

  echo "🚀 Levantando SearXNG..."
  $compose_cmd -f "$compose_file" --env-file "$env_file" up -d

  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "$searx_url/" >/dev/null 2>&1; then
      echo "   ✔ SearXNG listo en $searx_url"
      return 0
    fi
    sleep 2
  done

  echo "❌ SearXNG no respondió en $searx_url." >&2
  $compose_cmd -f "$compose_file" --env-file "$env_file" logs --tail=50 core >&2 || true
  exit 1
}

# Run SearXNG before the core installer so Aether's web-search dependency is
# ready by the time installation/validation finishes.
bootstrap_searxng "$@"

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
