"""
Captura de pantalla y análisis de visión con Ollama.
"""
import subprocess
import os
import base64
import json
import requests

from core.config.settings import MODELO, OLLAMA_HOST


def ver_pantalla(pregunta: str = "¿Qué ves en esta pantalla?") -> str:
    """
    Toma screenshot del monitor activo en Hyprland.
    Lo analiza con visión local en Ollama.
    """
    screenshot = "/tmp/javier_vision.png"
    try:
        _mon = subprocess.run(["hyprctl", "monitors", "-j"],
                              capture_output=True, text=True, timeout=5)
        _activo = None
        if _mon.returncode == 0 and _mon.stdout.strip():
            try:
                _mons   = json.loads(_mon.stdout)
                _activo = next((m["name"] for m in _mons if m.get("focused")), None)
            except Exception:
                pass
        
        _cmd    = ["grim", "-o", _activo, screenshot] if _activo else ["grim", screenshot]
        resultado = subprocess.run(_cmd, capture_output=True, timeout=10)
        if resultado.returncode != 0:
            return "No pude tomar el screenshot, compa. ¿Tenés instalado 'grim'?"
        
        with open(screenshot, "rb") as f:
            imagen_b64 = base64.b64encode(f.read()).decode("utf-8")
        
        payload = {
            "model":  MODELO,
            "prompt": pregunta,
            "images": [imagen_b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }
        r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=60)
        if r.status_code == 200:
            return r.json().get("response", "Sin respuesta de mi modelo de visión, cara.")
        return f"Error HTTP {r.status_code} al consultar la corteza visual de Ollama."
    except Exception as e:
        return f"Error en mi sistema de visión, compa: {e}"
    finally:
        if os.path.exists(screenshot):
            try:
                os.unlink(screenshot)
            except Exception:
                pass
