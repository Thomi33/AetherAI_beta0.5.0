"""Lifecycle bridge from Aether to the separate Roblox runtime."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from core.tools import computer_control

logger = logging.getLogger(__name__)


class RobloxRuntime:
    def __init__(self, runtime_dir: str | None = None) -> None:
        configured = runtime_dir or os.environ.get("AETHER_ROBLOX_DIR", "~/aether-roblox")
        self.runtime_dir = Path(configured).expanduser()
        self.process: subprocess.Popen[str] | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> tuple[bool, str]:
        if self.running:
            return False, "El runtime Roblox ya está activo."
        if not self.runtime_dir.is_dir():
            return False, f"No existe el runtime Roblox: {self.runtime_dir}"

        window_id, lookup_error = computer_control.buscar_ventana("org.vinegarhq.Sober")
        if lookup_error or window_id is None:
            return False, lookup_error or "No se encontró la ventana de Sober."
        focus_message, focus_error = computer_control.enfocar_ventana(window_id)
        if focus_error:
            return False, f"No se pudo enfocar Sober: {focus_message}"

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(self.runtime_dir), "/home/thomi/mi_proyecto_crew", env.get("PYTHONPATH", "")]
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "aether_lab.orchestrator", "--log-level", "INFO"],
            cwd=self.runtime_dir,
            env=env,
            text=True,
            start_new_session=True,
        )
        logger.info("Roblox runtime iniciado pid=%s dir=%s", self.process.pid, self.runtime_dir)
        return True, f"Roblox autónomo iniciado (pid {self.process.pid})."

    def stop(self) -> tuple[bool, str]:
        if not self.running:
            self.process = None
            return False, "El runtime Roblox no está activo."
        assert self.process is not None
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        pid = self.process.pid
        self.process = None
        return True, f"Roblox autónomo detenido (pid {pid})."
