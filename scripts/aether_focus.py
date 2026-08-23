#!/usr/bin/env python3
"""
Aether Focus/Launch Handler

Ejecutado por el daemon de wake word cuando se detecta "hey aether".
- Si la terminal de Aether ya está corriendo (identificada por el título de ventana),
  la enfoca instantáneamente vía hyprctl.
- Si no está corriendo, la lanza con kitty (título "Aether") ejecutando `python run.py`
  y luego la enfoca.

No hace polling costoso: usa `hyprctl clients -j` (un solo JSON) y cachea el estado
entre invocaciones para evitar lanzamientos duplicados dentro de un corto intervalo.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

# --- Configuración ---
AETHER_TITLE = "Aether"
AETHER_DIR = Path.home() / "mi_proyecto_crew"
TERMINAL = "/usr/sbin/kitty"
# Python del venv crewai-env (tiene textual + core)
PYTHON_BIN = str(AETHER_DIR / "crewai-env" / "bin" / "python")
LAUNCH_CMD = [PYTHON_BIN, "run.py"]
# Cooldown para evitar lanzamientos duplicados (segundos)
LAUNCH_COOLDOWN = 3.0
STATE_FILE = Path.home() / ".local" / "share" / "aether" / "focus_state.json"


def _ensure_state_dir():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_state():
    """Carga el estado cacheado (último lanzamiento)."""
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
            return data
    except Exception:
        pass
    return {"last_launch": 0.0}


def _save_state(state):
    """Guarda el estado cacheado."""
    try:
        _ensure_state_dir()
        STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def find_aether_window():
    """
    Busca en hyprctl clients un cliente cuyo título contenga 'Aether'.
    Devuelve el address (hex) de la ventana si la encuentra, o None.
    """
    try:
        result = subprocess.run(
            ["hyprctl", "clients", "-j"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        clients = json.loads(result.stdout)
        for client in clients:
            title = client.get("title", "") or ""
            if AETHER_TITLE in title:
                return client.get("address", "")
    except Exception:
        pass
    return None


def focus_window(address):
    """Enfoca una ventana por su address usando hyprctl dispatch."""
    if not address:
        return False
    try:
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"address:{address}"],
            capture_output=True,
            timeout=2,
        )
        return True
    except Exception:
        return False


def launch_aether():
    """Lanza kitty con el título 'Aether' ejecutando `python run.py` en AETHER_DIR."""
    try:
        cmd = [TERMINAL, "--title", AETHER_TITLE, "--class", AETHER_TITLE, "-e"] + LAUNCH_CMD
        subprocess.Popen(
            cmd,
            cwd=str(AETHER_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def main():
    state = _load_state()
    now = time.time()

    # 1. Buscar ventana existente
    address = find_aether_window()
    if address:
        # La terminal ya está corriendo → enfocar
        focus_window(address)
        print(f"[aether_focus] Ventana encontrada y enfocada: {address}")
        return 0

    # 2. No está corriendo → lanzar (respetando cooldown)
    if now - state.get("last_launch", 0.0) < LAUNCH_COOLDOWN:
        print("[aether_focus] En cooldown, no se lanza de nuevo")
        return 0

    state["last_launch"] = now
    _save_state(state)

    if launch_aether():
        print("[aether_focus] Aether lanzado con kitty, esperando a que aparezca la ventana...")
        # Dar tiempo a kitty a crear la ventana, luego enfocar
        for _ in range(20):  # hasta ~1 segundo
            time.sleep(0.05)
            address = find_aether_window()
            if address:
                focus_window(address)
                print(f"[aether_focus] Ventana lanzada y enfocada: {address}")
                return 0
        print("[aether_focus] Ventana lanzada (focus pendiente en próxima detección)")
        return 0
    else:
        print("[aether_focus] ERROR: no se pudo lanzar Aether", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
