#!/usr/bin/env python3
"""
Lanzador de la TUI rica de Aether (estilo Claude Code).

Uso recomendado:
    cd /home/thomi/mi_proyecto_crew
    source env/bin/activate
    python -m tui
"""
import sys
from pathlib import Path

# Asegurar que el root del proyecto esté en el path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tui.app import run

if __name__ == "__main__":
    run()
