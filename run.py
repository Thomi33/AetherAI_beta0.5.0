#!/usr/bin/env python
"""
AETHER - Entrypoint Principal (TUI moderna con Textual)

Ejecuta: python run.py
"""
import sys
from pathlib import Path

# Asegurar que el directorio raíz esté en sys.path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Importar y ejecutar la TUI moderna
from tui.app import run

if __name__ == "__main__":
    run()
