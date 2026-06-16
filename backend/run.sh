#!/bin/bash

# Script para instalar dependencias e iniciar el backend

set -e

echo "🚀 Iniciando setup del backend..."

# Verificar si estamos en la carpeta correcta
if [ ! -f "requirements.txt" ]; then
    echo "❌ requirements.txt no encontrado. Ejecuta este script desde la carpeta backend/"
    exit 1
fi

# Instalar dependencias
echo "📦 Instalando dependencias..."
pip install -r requirements.txt

echo "✅ Backend setup completado!"
echo "▶️  Ejecutando servidor..."
echo ""
echo "API disponible en: http://localhost:8000"
echo "Docs: http://localhost:8000/docs"
echo ""

# Ejecutar servidor
cd ..
python -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
