"""
Widget Debug Panel para mostrar logs de ejecución en la TUI.
Muestra mensajes de herramientas, errores y otros eventos del motor.
"""

from textual.widgets import Static, Label
from rich.markup import escape


class DebugPanel(Static):
    """Panel que muestra los logs de debug y ejecución."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._debug_logs = []
        self._max_lines = 20

    def agregar_log(self, mensaje=None, tipo="info"):
        """Agrega una línea al panel de debug."""
        if mensaje is not None:
            prefix = f"[{tipo.upper()}]" if tipo else ""
            texto = f"{prefix} {escape(str(mensaje))}"
            self._debug_logs.append(texto)
            self._repintar()

    def _repintar(self):
        """Reconstruye el contenido visual del panel."""
        if len(self._debug_logs) > self._max_lines:
            self._debug_logs = self._debug_logs[-self._max_lines:]
        texto = "\n".join(self._debug_logs)
        self.update(texto)

    def reset(self):
        self._debug_logs.clear()
        self._repintar()