#!/bin/bash

# Script para iniciar frontend y backend en paralelo

set -e

echo "🎯 Aether - Agente Local Inteligente"
echo "====================================="
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if node_modules exists
if [ ! -d "frontend/node_modules" ]; then
    echo -e "${YELLOW}📦 Instalando dependencias del frontend...${NC}"
    cd frontend
    npm install
    cd ..
fi

# Check if backend venv exists
if [ ! -d "backend_env" ] && [ ! -d "env" ]; then
    echo -e "${YELLOW}🐍 Se recomienda activar un entorno virtual para el backend${NC}"
fi

echo ""
echo -e "${BLUE}▶️  Iniciando Aether...${NC}"
echo ""

# Start frontend
echo -e "${GREEN}🎨 Frontend Vite (http://localhost:5173)${NC}"
cd frontend
npm run dev &
FRONTEND_PID=$!

# Start backend
echo -e "${GREEN}🔧 Backend FastAPI (http://localhost:8000)${NC}"
cd ../backend
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Deteniendo servidores..."
    kill $FRONTEND_PID 2>/dev/null || true
    kill $BACKEND_PID 2>/dev/null || true
}

trap cleanup EXIT

# Wait for any process to exit
wait
