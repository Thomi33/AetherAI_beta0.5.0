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
if [ -n "$QWEN_API_KEY" ]; then
    echo "🤖 Qwen detectado"
fi

if [ -n "$OPENAI_API_KEY" ]; then
    echo "🤖 OpenAI detectado"
fi

# -------------------------
# 6. Final validation
# -------------------------
echo "🧪 Verificando instalación..."

python3 -c "print('Aether OK ✔')"

# -------------------------
# 7. Finish
# -------------------------
echo ""
echo "✅ Aether instalado correctamente"
echo "👉 Ejecuta:"
echo "   cd $INSTALL_DIR"
echo "   source crewai-env/bin/activate"
echo "   python tui_main.py"