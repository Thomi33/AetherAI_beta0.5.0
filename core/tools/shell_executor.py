"""
Ejecución segura de comandos shell en zsh.
"""
import os
import re
import subprocess
import tempfile
import time

from core.config.settings import TIMEOUT_CMD


_PATRONES_PELIGROSOS = [
    r"rm\s+-rf\s+/(?:\s|$)",
    r"mkfs\.",
    r"dd\s+if=",
    r"chmod\s+[0-7]{3,4}\s+/",
    r":\(\)\{.*\}",
    r"kill\s+-9\s+1\b",
    r"> /dev/sd[a-z]",
]

_LANZADORES_GUI = re.compile(
    r"\b(flatpak\s+run|steam|lutris|heroic|bottles|gamescope"
    r"|nvtop|btop|htop|glxgears|obs|kdenlive|gimp|inkscape"
    r"|brave|electron|appimage|prismlauncher"
    r"|python\s+-m\s+|python3?\s+\S+\.py"
    r"|\./"
    r")",
    re.IGNORECASE,
)


def _es_peligroso(cmd: str) -> bool:
    """Detecta patrones de comandos potencialmente destructivos."""
    return any(re.search(p, cmd, re.IGNORECASE) for p in _PATRONES_PELIGROSOS)


def ejecutar_comando(cmd: str) -> tuple[str, bool]:
    """
    Ejecuta comando en zsh con protección de seguridad.
    Retorna (salida, hubo_error).
    """
    if _es_peligroso(cmd):
        return "⛔ CANCELADO: Operación identificada como potencialmente destructiva.", True

    es_gui = bool(_LANZADORES_GUI.search(cmd))

    try:
        if es_gui:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jarvis_err")
            proc = subprocess.Popen(
                cmd, shell=True, executable="/bin/zsh",
                stdout=subprocess.DEVNULL, stderr=tmp,
                preexec_fn=os.setpgrp,
            )
            tmp.close()
            time.sleep(1.5)
            if proc.poll() is not None and proc.returncode != 0:
                with open(tmp.name) as f:
                    err = f.read().strip()
                os.unlink(tmp.name)
                return f"El proceso terminó con error:\n{err}", True
            os.unlink(tmp.name)
            return f"[Proceso lanzado en segundo plano. PID: {proc.pid}]", False

        resultado = subprocess.run(
            cmd, shell=True, executable="/bin/zsh",
            capture_output=True, text=True, timeout=TIMEOUT_CMD,
        )
        salida = (resultado.stdout + resultado.stderr).strip()
        return salida or "[Comando completado sin salida]", resultado.returncode != 0

    except subprocess.TimeoutExpired:
        return f"Tiempo límite de {TIMEOUT_CMD}s agotado. Proceso interrumpido.", True
    except Exception as e:
        return f"Error de subproceso: {e}", True
