#!/usr/bin/env bash
set -e

# =========================
# Aether Unified Installer
# =========================

REPO_URL="https://github.com/Thomi33/AetherAI_beta0.5.0.git"
INSTALL_DIR="${AETHER_DIR:-$HOME/AetherAI}"
BRANCH="${AETHER_BRANCH:-main}"

echo "🚀 Aether Unified Installer"
echo "📦 Target: $INSTALL_DIR"
echo "🌿 Branch: $BRANCH"

# -------------------------
# 1. Pre-checks
# -------------------------
command -v git >/dev/null 2>&1 || { echo "❌ git requerido"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ python3 requerido"; exit 1; }

# -------------------------
# 2. Clone / Update repo
# -------------------------
if [ ! -d "$INSTALL_DIR" ]; then
    echo "📥 Clonando Aether..."
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
else
    echo "🔄 Actualizando Aether..."
    cd "$INSTALL_DIR"
    git pull origin "$BRANCH"
fi

cd "$INSTALL_DIR"

# -------------------------
# 3. Setup environment
# -------------------------
echo "🧠 Configurando entorno..."

if [ ! -d "crewai-env" ]; then
    python3 -m venv crewai-env
fi

source crewai-env/bin/activate

pip install --upgrade pip

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

if [ -f "backend/requirements.txt" ]; then
    pip install -r backend/requirements.txt
fi

# -------------------------
# 4. Environment config
# -------------------------
if [ ! -f ".env" ]; then
    echo "⚙️ Creando .env base..."
    cat > .env << EOF
AETHER_MODE=production
LOG_LEVEL=info
EOF
fi

# -------------------------
# 5. Optional Qwen/OpenAI check
# -------------------------
# (Qwen removido: el motor usa Ollama local, sin API keys externas)

if [ -n "$OPENAI_API_KEY" ]; then
    echo "🤖 Provider detectado"
fi

# -------------------------
# 6. Final validation
# -------------------------
echo "🧪 Verificando instalación..."

python3 -c "print('Aether OK ✔')"

# -------------------------
# 6.5. Comando global `aether` (launcher en $PATH)
# -------------------------
echo "🌍 Instalando comando global 'aether'..."
LAUNCH_DEST="${AETHER_BIN_DIR:-$HOME/.local/bin}"

if command -v aether >/dev/null 2>&1 && [ -z "${AETHER_REINSTALL_BIN:-}" ]; then
    echo "   ✔ Ya existe un comando 'aether' en el PATH"
elif mkdir -p "$LAUNCH_DEST" 2>/dev/null && ln -sf "$PWD/bin/aether" "$LAUNCH_DEST/aether"; then
    echo "   ✔ 'aether' disponible en $LAUNCH_DEST"
    echo "      (moverá/volcá el repo y volvé a correr install.sh para re-apuntarlo"
    echo "       si clonás a otra carpeta; o forzá con AETHER_REINSTALL_BIN=1)"
else
    echo "   ⚠ No se pudo enlazar el launcher; probá: python $INSTALL_DIR/run.py" >&2
fi

# -------------------------
# 7. Finish
# -------------------------
echo ""
echo "✅ Aether instalado correctamente"
echo "👉 Ejecuta desde cualquier directorio:"
echo "   aether"
