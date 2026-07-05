"""
tui/widgets/__init__.py — Widgets de la TUI de Aether.

PlanPanel: sidebar colapsable con lista de pasos del plan.
ConfigPanel: panel interactivo de configuración en caliente.
"""

from __future__ import annotations

from textual.widgets import Static
from textual.containers import Vertical
from rich.text import Text

from .config import ConfigPanel, ConfigOption, ConfigSection


_ICONOS_TOOL = {
    "text": "💬", "web": "🔍", "shell": "🖥️", "launch": "🚀",
    "vision": "👁️", "codigo": "💻", "memory": "🧩", "file_write": "💾", "mcp": "🔌",
}


class PlanPanel(Vertical):
    """Panel lateral: lista de pasos del plan + estado de cada uno."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan_pasos: list[dict] = []
        self.plan_index: int = 0
        self.plan_activo: bool = False

    def compose(self):
        yield Static("[b dim]PLAN[/b dim]", id="plan-title")
        yield Static("[dim]—[/dim]", id="plan-body")

    def on_mount(self) -> None:
        try:
            body = self.query_one("#plan-body", Static)
            body.update("—")
        except Exception:
            pass

    def actualizar(self, plan_pasos: list[dict], plan_index: int, plan_activo: bool) -> None:
        self.plan_pasos = plan_pasos
        self.plan_index = plan_index
        self.plan_activo = plan_activo
        self._repintar()

    def marcar_simple(self, tool: str) -> None:
        icono = _ICONOS_TOOL.get(tool, "•")
        body = self.query_one("#plan-body", Static)
        body.update(f"{icono} Tarea simple → [b]{tool}[/b]")

    def _repintar(self) -> None:
        body = self.query_one("#plan-body", Static)
        if not self.plan_pasos:
            body.update("—" if not self.plan_activo else "planificando...")
            return

        lineas = []
        for i, paso in enumerate(self.plan_pasos):
            tool = paso.get("tool", "?") if isinstance(paso, dict) else "?"
            icono = _ICONOS_TOOL.get(tool, "•")

            if i < self.plan_index:
                linea = f"[dim]✓ {icono} {tool}[/dim]"
            elif i == self.plan_index and self.plan_activo:
                linea = f"[bold yellow]▶ {icono} {tool}[/bold yellow]"
            else:
                linea = f"[dim]  {icono} {tool}[/dim]"

            lineas.append(linea)

        body.update("\n".join(lineas))

    def reset(self) -> None:
        self.plan_pasos = []
        self.plan_index = 0
        self.plan_activo = False
        body = self.query_one("#plan-body", Static)
        body.update("—")


__all__ = ["PlanPanel", "ConfigPanel", "ConfigOption", "ConfigSection"]
