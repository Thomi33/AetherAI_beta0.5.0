#!/usr/bin/env bash
# ============================================================================
# Aether Unified Installer — v3 (perfilado real de hardware + tiers)
# Detecta CPU/RAM/VRAM/disco, verifica versiones y ajusta SOLO lo del
# modelo: MODELO, VISION, NUM_CTX, NUM_PREDICT, turnos, threads, batch,
# keep_alive. NO recorta features (ydotool, MCP, STT, etc. intactos).
# Uso: ./install.sh [--tier LOW|MID|HIGH|ULTRA|POTATO] [--low-spec]
#      [--minimal] [--no-system] [--no-ollama] [--model NOMBRE] [-y] [-h]
# Env: AETHER_DIR, AETHER_BRANCH, AETHER_TIER, AETHER_PYTHON, AETHER_BIN_DIR
# ============================================================================
set -euo pipefail

REPO_URL="https://github.com/Thomi33/AetherAI_beta0.5.0.git"
INSTALL_DIR="${AETHER_DIR:-$HOME/AetherAI}"
BRANCH="${AETHER_BRANCH:-main}"
LAUNCH_DEST="${AETHER_BIN_DIR:-$HOME/.local/bin}"

LOW_SPEC=0; MINIMAL=0; NO_SYSTEM=0; NO_OLLAMA=0; ASSUME_YES=0; WANT_MODEL=""; TIER_REQ="${AETHER_TIER:-}"
PREV=""
for arg in "$@"; do
  if [ "$PREV" = "--model" ]; then WANT_MODEL="$arg"; PREV=""; continue; fi
  if [ "$PREV" = "--tier" ]; then TIER_REQ="$arg"; PREV=""; continue; fi
  case "$arg" in
    --low-spec) LOW_SPEC=1 ;; --minimal) MINIMAL=1 ;;
    --no-system) NO_SYSTEM=1 ;; --no-ollama) NO_OLLAMA=1 ;;
    --model) PREV="--model" ;; --model=*) WANT_MODEL="${arg#--model=}" ;;
    --tier) PREV="--tier" ;; --tier=*) TIER_REQ="${arg#--tier=}" ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# //'; exit 0 ;;
  esac
done

log()  { printf '%s\n' "$*"; }
ok()   { printf '   ✔ %s\n' "$*"; }
warn() { printf '   ⚠ %s\n' "$*" >&2; }
die()  { printf '❌ %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
ask_yes() {
  if [ "$ASSUME_YES" = "1" ]; then return 0; fi
  read -r -p "$1 [S/n] " r || return 1
  case "$r" in ""|[SsYy]*) return 0 ;; *) return 1 ;; esac
}

echo "🚀 Aether Unified Installer (v3)"
echo "📦 Target: $INSTALL_DIR"
echo "🌿 Branch: $BRANCH"

# -------------------------
# 0. Perfilado de hardware (tools/hardware_probe.py si existe, si no fallback)
# -------------------------
PROBE_JSON=""
run_probe() {
  local script=""
  # 1) ya clonado (reinstalación) 2) junto al installer (curl del repo)
  [ -f "$INSTALL_DIR/tools/hardware_probe.py" ] && script="$INSTALL_DIR/tools/hardware_probe.py"
  [ -z "$script" ] && [ -f "$(dirname "$0")/tools/hardware_probe.py" ] && script="$(dirname "$0")/tools/hardware_probe.py"
  if [ -n "$script" ]; then PROBE_JSON=$(python3 "$script" ${WANT_MODEL:+--model-override "$WANT_MODEL"} 2>/dev/null) || PROBE_JSON=""; fi
  if [ -z "$PROBE_JSON" ]; then
    # Fallback sin probe: Celeron/Atom/Pentium o <=10GB => LOW, si no HIGH (dev)
    MEMFALLBACK=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0) / 1024 / 1024 ))
    CPUF=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 || echo "?")
    if { [ "$MEMFALLBACK" -ne 0 ] && [ "$MEMFALLBACK" -le 10 ]; } || echo "$CPUF" | grep -qiE 'celeron|atom|pentium|n4500|n4020'; then TIER_FB="LOW"; else TIER_FB="HIGH"; fi
    PROBE_JSON=$(python3 -c "import json;print(json.dumps({'tier':'$TIER_FB','cpu':'''"$CPUF"''','ram_gb':$MEMFALLBACK,'vram_gb':0,'usable_gb':0,'recomendado':{}}))")
  fi
  TIER=$(python3 -c "import json,sys;print(json.load(open('/dev/stdin'))['tier'])" <<<"$PROBE_JSON" 2>/dev/null || echo "LOW")
}
run_probe
# --tier / AETHER_TIER / --low-spec pisan lo detectado
[ -n "$TIER_REQ" ] && TIER=$(echo "$TIER_REQ" | tr '[:lower:]' '[:upper:]')
[ "$LOW_SPEC" = "1" ] && [ "$TIER" != "POTATO" ] && TIER="LOW"
echo "🧬 Tier detectado: $TIER"
python3 -c "import json,sys;r=json.load(sys.stdin);print('   CPU: '+str(r.get('cpu'))+' | RAM: '+str(r.get('ram_gb'))+'GB | VRAM: '+str(r.get('vram_gb'))+'GB | usable~'+str(r.get('usable_gb'))+'GB → '+r['recomendado'].get('MODELO','?')+' ctx='+str(r['recomendado'].get('NUM_CTX','?')))" <<<"$PROBE_JSON" 2>/dev/null || true

# -------------------------
# 1. SO + versiones (verificado, no asumido)
# -------------------------
OS_ID="unknown"
if [ -f /etc/os-release ]; then . /etc/os-release; OS_ID="${ID:-unknown}"; fi
IS_ARCH=0; IS_DEBIAN=0; IS_FEDORA=0
case "$OS_ID" in arch|endeavouros|manjaro|cachyos) IS_ARCH=1;; debian|ubuntu|linuxmint|pop) IS_DEBIAN=1;; fedora|nobara|rhel) IS_FEDORA=1;; esac
DISK_FREE_GB=$(python3 -c "import json,sys;r=json.load(sys.stdin);print(int(r.get('disco_libre_gb',-1)))" <<<"$PROBE_JSON" 2>/dev/null || echo "?")
MEM_GB=$(python3 -c "import json,sys;print(int(float(json.load(sys.stdin).get('ram_gb',0))))" <<<"$PROBE_JSON" 2>/dev/null || echo 0)
SWAP_MB=$(python3 -c "import json,sys;print(int(float(json.load(sys.stdin).get('swap_gb',0))*1024))" <<<"$PROBE_JSON" 2>/dev/null || echo 0)
CPU_MODEL=$(python3 -c "import json,sys;print(json.load(sys.stdin).get('cpu','?')[:60])" <<<"$PROBE_JSON" 2>/dev/null || echo "?")
echo "🖥️  SO: $OS_ID | CPU: $CPU_MODEL | RAM: ${MEM_GB}GB | swap: ${SWAP_MB}MB | libre HOME: ${DISK_FREE_GB}GB"
[ "$DISK_FREE_GB" != "-1" ] && [ "$DISK_FREE_GB" != "?" ] && [ "$DISK_FREE_GB" -lt 5 ] && warn "Solo ${DISK_FREE_GB}GB libres; necesitás ~5GB (repo+venv+modelo chico)."
[ "$SWAP_MB" -lt 2048 ] && warn "Swap: ${SWAP_MB}MB. Con LLM en CPU conviene zram: sudo pacman -S zram-generator && sudo systemctl enable --now systemd-zram-setup@zram0"
if [ "$NO_SYSTEM" = "0" ]; then
  SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
  if ! have sudo && [ "$(id -u)" != "0" ]; then warn "Sin sudo: no instalo paquetes. Necesitás: git python sqlite curl ollama.";
  elif [ "$IS_ARCH" = "1" ] && have pacman; then
    echo "📦 pacman: deps base + Wayland helpers (features intactas)..."
    $SUDO pacman -Sy --needed --noconfirm git base-devel python python-pip python-virtualenv sqlite curl ollama wl-clipboard grim slurp 2>&1 | tail -3 || warn "pacman falló parcial; sigo."
    [ "$MINIMAL" = "0" ] && ! have ydotool && { $SUDO pacman -S --needed --noconfirm ydotool 2>/dev/null && ok "ydotool OK" || warn "ydotool opcional omitido."; }
  elif [ "$IS_DEBIAN" = "1" ] && have apt-get; then
    $SUDO apt-get update -y 2>&1 | tail -1; $SUDO apt-get install -y git python3 python3-venv python3-pip sqlite3 curl wl-clipboard grim slurp 2>&1 | tail -3 || warn "apt parcial; sigo."
  elif [ "$IS_FEDORA" = "1" ] && have dnf; then
    $SUDO dnf install -y git python3 python3-pip sqlite curl wl-clipboard grim slurp 2>&1 | tail -3 || warn "dnf parcial; sigo."
  else warn "Distro '$OS_ID' sin gestor conocido; verifico a mano."; fi
else echo "⏭️  --no-system: salto paquetes."; fi
# Versiones mínimas reales (verificado, no "instalar y rezar")
command -v git >/dev/null 2>&1 || die "git requerido"
command -v python3 >/dev/null 2>&1 || die "python3 requerido (mínimo 3.10)"
python3 -c "import sys;assert sys.version_info>=(3,10),'python>=3.10'" || die "python3 muy viejo: $(python3 --version 2>&1)"
python3 -c "import venv" 2>/dev/null || die "falta python-venv (Arch: sudo pacman -S python-virtualenv)"
ok "versiones: $(git --version | head -1) / $(python3 --version 2>&1) / sqlite $(sqlite3 --version 2>/dev/null || echo '?') / curl $(curl --version 2>/dev/null | head -1 | awk '{print $2}')"
if have ollama; then ok "ollama: $(ollama --version 2>/dev/null || echo instalado)"; else warn "ollama ausente (se intenta en paso 5)"; fi
[ -n "${SWAYSOCK:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ] && { ok "Wayland/Sway detectado."; for t in wl-copy grim slurp; do have "$t" || warn "'$t' falta: sudo pacman -S wl-clipboard grim slurp"; done; }
# GPU extra (solo informativo: confirma backend disponible)
if have nvidia-smi; then nvidia-smi -L 2>/dev/null | head -2 || true; fi

# -------------------------
# 2. Clone / Update (seguro)
# -------------------------
if [ ! -d "$INSTALL_DIR" ]; then
  echo "📥 Clonando Aether..."
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
else
  echo "🔄 Actualizando Aether..."
  cd "$INSTALL_DIR"
  if git rev-parse --git-dir >/dev/null 2>&1; then
    git fetch origin "$BRANCH" --depth 1 2>/dev/null || git fetch origin "$BRANCH" || true
    if ! git diff --quiet || ! git diff --cached --quiet; then
      warn "Cambios locales detectados; stash para actualizar sin romper nada."
      git stash push -m "aether-install $(date +%F_%T)" || true
    fi
    git checkout "$BRANCH" 2>/dev/null || true
    git pull --rebase origin "$BRANCH" || warn "git pull falló; sigo con lo local."
  else warn "$INSTALL_DIR no es repo git; sigo sin actualizar."; fi
fi
cd "$INSTALL_DIR"

# -------------------------
# 3. venv + pip (liviano)
# -------------------------
echo "🧠 Configurando entorno..."
export MAKEFLAGS="${MAKEFLAGS:--j2}"  # Celeron: no compilar a 4 hilos
export PIP_DISABLE_PIP_VERSION_CHECK=1
PYBIN="${AETHER_PYTHON:-python3}"
[ ! -d "crewai-env" ] && { echo "🐍 Creando venv (crewai-env) con $PYBIN..."; "$PYBIN" -m venv crewai-env || die "venv falló (Arch: sudo pacman -S python-virtualenv)"; }
# shellcheck disable=SC1091
source crewai-env/bin/activate
ok "venv: $(python --version 2>&1)"
pip install --upgrade "pip<26" 2>&1 | tail -1; pip cache purge 2>/dev/null || true
pip_one() {
  [ -f "$1" ] || return 0; echo "📚 $1..."
  # POTATO/LOW (o disco <10GB): ruedas binarias, sin compilar en Celeron
  if [ "$TIER" = "POTATO" ] || [ "$TIER" = "LOW" ] || [ "$LOW_SPEC" = "1" ]; then EXTRA="--prefer-binary"; [ "$DISK_FREE_GB" != "?" ] && [ "$DISK_FREE_GB" != "-1" ] && [ "$DISK_FREE_GB" -lt 10 ] && EXTRA="$EXTRA --no-cache-dir"; pip install $EXTRA -r "$1" 2>&1 | tail -4
  else pip install -r "$1" 2>&1 | tail -4; fi
}
pip_one "requirements.txt"; pip_one "backend/requirements.txt"
pip cache purge 2>/dev/null || true  # NVMe 128GB: no dejar GBs en cache

# -------------------------
# 4. .env + config del tier (SOLO modelo/contexto/memoria — features intactas)
# -------------------------
if [ ! -f ".env" ]; then
  echo "⚙️ Creando .env base..."
  printf 'AETHER_MODE=production\nLOG_LEVEL=info\n#OLLAMA_HOST=http://localhost:11434\n#OLLAMA_NUM_PARALLEL=1\n#OLLAMA_MAX_LOADED_MODELS=1\n' > .env
fi
# Re-perfilado post-clone (ya existe tools/hardware_probe.py del repo).
# Si el usuario forzó --tier, se re-emite el probe con ese tier para que
# recomendado[] coincida (sin tocar el JSON a mano).
if [ -n "$TIER_REQ" ]; then
  TIER_FORZADO=$(echo "$TIER_REQ" | tr '[:lower:]' '[:upper:]')
  PROBE_JSON=$(python3 tools/hardware_probe.py --force-tier "$TIER_FORZADO" ${WANT_MODEL:+--model-override "$WANT_MODEL"} 2>/dev/null || echo "$PROBE_JSON")
  TIER="$TIER_FORZADO"
else
  run_probe
fi
[ "$LOW_SPEC" = "1" ] && [ "$TIER" != "POTATO" ] && { TIER="LOW"; PROBE_JSON=$(python3 tools/hardware_probe.py --force-tier LOW ${WANT_MODEL:+--model-override "$WANT_MODEL"} 2>/dev/null || echo "$PROBE_JSON"); }
if [ -z "$WANT_MODEL" ]; then WANT_MODEL=$(python3 -c "import json,sys;print(json.load(sys.stdin)['recomendado'].get('MODELO',''))" <<<"$PROBE_JSON" 2>/dev/null); fi
echo "🧬 Tier final: $TIER | modelo: ${WANT_MODEL:-?}"
if [ -f "core/config/config.json" ]; then
  echo "⚙️ Aplicando config del tier $TIER (backup .bak; solo claves de modelo)..."
  cp -n "core/config/config.json" "core/config/config.json.bak" 2>/dev/null || cp "core/config/config.json" "core/config/config.json.bak" || true
  PROBE_JSON="$PROBE_JSON" WANT_MODEL="$WANT_MODEL" TIER="$TIER" ./crewai-env/bin/python - core/config/config.json << 'PYEOF' || warn "tuneo falló; revisá manual."
import json, os, sys
path = sys.argv[1]
probe = json.loads(os.environ.get("PROBE_JSON") or "{}")
rec = (probe.get("recomendado") or {})
if os.environ.get("WANT_MODEL"): rec["MODELO"] = os.environ["WANT_MODEL"]
gen_rec = rec.get("OLLAMA_GEN_OPTIONS", {}) or {}
cfg = json.load(open(path))
# SOLO estas claves (nada de ydotool/STT/MCP/temas): el agente sigue completo.
for k in ("MODELO", "MODELO_VISION", "NUM_CTX", "NUM_PREDICT",
          "NUM_PREDICT_PLANNER", "MAX_TURNOS_CONTEXTO_CHAT", "TIMEOUT_CMD",
          "OLLAMA_KEEP_ALIVE", "OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS"):
    if rec.get(k) is not None: cfg[k] = rec[k]
g = cfg.get("OLLAMA_GEN_OPTIONS", {}) or {}
for k in ("num_batch", "num_thread", "num_gpu"):
    if gen_rec.get(k) is not None: g[k] = gen_rec[k]
cfg["OLLAMA_GEN_OPTIONS"] = g
json.dump(cfg, open(path, "w"), indent=2, ensure_ascii=False)
print(f"tier {os.environ.get('TIER')}: {cfg.get('MODELO')} ctx={cfg.get('NUM_CTX')} batch={g.get('num_batch')} threads={g.get('num_thread')} gpu={g.get('num_gpu')}")
PYEOF
  ok "config ajustada (orig: config.json.bak)"
fi

# -------------------------
# 5. Ollama + modelo del tier (verifica antes de bajar)
# -------------------------
[ -n "${OPENAI_API_KEY:-}" ] && echo "🤖 Provider detectado"
# Tamaños aprox (GB) para validar contra disco libre antes del pull
modelo_gb() { case "$1" in *1.5b*) echo 1;; *3b*) echo 2;; *7b*|*8b*) echo 5;; *9b*) echo 6;; *35B*|*35b*) echo 20;; *) echo 4;; esac; }
if [ "$NO_OLLAMA" = "1" ] || [ "$MINIMAL" = "1" ]; then echo "⏭️  Ollama omitido.";
elif ! have ollama; then warn "Sin ollama. Arch: sudo pacman -S ollama | otro: curl -fsSL https://ollama.com/install.sh | sh";
else
  ollama list >/dev/null 2>&1 || { echo "🦙 Levantando ollama...";
    if have systemctl; then systemctl --user enable --now ollama 2>/dev/null || sudo systemctl enable --now ollama 2>/dev/null || { (ollama serve >/tmp/ollama-serve.log 2>&1 & sleep 3) || true; };
    else (ollama serve >/tmp/ollama-serve.log 2>&1 & sleep 3) || true; fi; }
  if ollama list >/dev/null 2>&1; then ok "Ollama responde.";
    # El modelo ya viene del probe (tier); solo default de seguridad
    [ -z "$WANT_MODEL" ] && { [ "$TIER" = "POTATO" ] && WANT_MODEL="qwen2.5:1.5b" || WANT_MODEL="qwen2.5:3b"; }
    VISION_MODEL=$(python3 -c "import json,sys;print(json.load(sys.stdin)['recomendado'].get('MODELO_VISION',''))" <<<"$PROBE_JSON" 2>/dev/null)
    if ollama list 2>/dev/null | grep -qi "$WANT_MODEL"; then ok "Modelo $WANT_MODEL presente.";
    else
      NEED=$(modelo_gb "$WANT_MODEL"); echo "🦙 Tier $TIER → '$WANT_MODEL' (~${NEED}GB) + visión '$VISION_MODEL' (opcional).";
      if [ "$DISK_FREE_GB" != "?" ] && [ "$DISK_FREE_GB" != "-1" ] && [ "$DISK_FREE_GB" -lt "$(( NEED + 3 ))" ]; then warn "Disco justo (${DISK_FREE_GB}GB libres, pull necesita ~${NEED}GB). Liberá con: pacman -Sc / ~/.cache / ollama rm <viejo>"; fi
      if ask_yes "¿Descargar '$WANT_MODEL'?"; then ollama pull "$WANT_MODEL" && ok "Modelo listo." || warn "pull falló. Reintentá: ollama pull $WANT_MODEL";
      else echo "⏭️  Omitido. Luego: ollama pull $WANT_MODEL"; fi; fi
    # Visión: solo sugerir, nunca obligar (no bloquea al agente)
    if [ -n "$VISION_MODEL" ] && ! ollama list 2>/dev/null | grep -qi "$(echo "$VISION_MODEL" | cut -d: -f1)"; then
      echo "ℹ️  Visión sugerida: ollama pull $VISION_MODEL (opcional, ~4GB)"
    fi
  else warn "Ollama no responde (/tmp/ollama-serve.log). Sigo sin modelo."; fi
fi

# -------------------------
# 6. Final validation
# -------------------------
echo "🧪 Verificando instalación (deps Python reales)..."

python -c "print('Aether OK ✔')"
# Chequeo de imports críticos: falla ACÁ y no en el primer `aether task`
python - << 'PYEOF' || echo "   ⚠ Alguna dependencia Python falta (ver arriba)" >&2
import importlib.util
faltan = [m for m in ("ollama", "langgraph", "langchain_core",
                      "langchain_ollama", "textual", "rich", "mcp",
                      "requests") if not importlib.util.find_spec(m)]
print("   ✔ imports core OK" if not faltan else f"   ⚠ faltan: {faltan}")
PYEOF
# Ollama responde + modelo configurado == descargado (no mentir "listo")
if command -v ollama >/dev/null 2>&1 && ollama list >/dev/null 2>&1; then
  CONF_MOD=$(python3 -c "import json;print(json.load(open('core/config/config.json')).get('MODELO','?'))" 2>/dev/null)
  ollama list 2>/dev/null | grep -qi "$CONF_MOD" && echo "   ✔ modelo configurado presente: $CONF_MOD" || echo "   ⚠ modelo '$CONF_MOD' no descargado aún: ollama pull $CONF_MOD" >&2
fi

# -------------------------
# 6.5. Comando global `aether` (launcher en $PATH)
# -------------------------
echo "🌍 Instalando comando global 'aether'..."

if have aether && [ -z "${AETHER_REINSTALL_BIN:-}" ]; then
    echo "   ✔ Ya existe un comando 'aether' en el PATH"
elif mkdir -p "$LAUNCH_DEST" 2>/dev/null && ln -sf "$PWD/bin/aether" "$LAUNCH_DEST/aether"; then
    echo "   ✔ 'aether' disponible en $LAUNCH_DEST"
    case ":$PATH:" in *":$LAUNCH_DEST:"*) ;; *) warn "$LAUNCH_DEST no está en PATH. Agregá: export PATH=\"\$HOME/.local/bin:\$PATH\" a ~/.zshrc";; esac
else
    echo "   ⚠ No se pudo enlazar el launcher; probá: python $INSTALL_DIR/run.py" >&2
fi
[ -f "bin/aether_run.py" ] && { echo "🩺 Doctor:"; python bin/aether_run.py doctor || warn "doctor con pendientes (normal si falta modelo)."; }

# -------------------------
# 7. Finish
# -------------------------
echo ""
echo "✅ Aether instalado correctamente — tier $TIER (${WANT_MODEL:-?})"
echo "👉 Ejecuta desde cualquier directorio:"
echo "   aether"
echo "ℹ️  El agente está COMPLETO en todos los tiers (tools, ydotool, MCP, STT);"
echo "   solo cambia el modelo/contexto/memoria según tu RAM/VRAM."
echo "   Re-perfilar: ./install.sh --tier MID|HIGH (o editá core/config/config.json)"
if [ "$TIER" = "POTATO" ] || [ "$TIER" = "LOW" ]; then
  echo ""; echo "🐢 Tips tier $TIER (CPU-only / poca RAM):"
  echo "   • Si Textual pesa: aether task \"...\" (one-shot sin TUI)"
  echo "   • zram: sudo pacman -S zram-generator && sudo systemctl enable --now systemd-zram-setup@zram0"
  echo "   • Cerrá apps pesadas al usar Aether; el KV-cache vive en RAM."
fi
