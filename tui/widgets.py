"""
widgets.py — Widgets de la TUI de Aether.

PlanPanel: lista de pasos del plan actual, con marcador de estado por paso
           (pendiente / en curso / hecho / error). Se llena leyendo
           plan_pasos / plan_index del delta de estado que llega vía
           NodeUpdateEvent("planner", {...}).

ProgressLog: log de líneas crudas capturadas de stdout (los print() que ya
             existen en graph_nodes.py), con un mínimo de color por prefijo
             conocido (🔧, 🔮, ⚠️, ❌) para que no sea texto plano gris.
"""

from __future__ import annotations

from textual.widgets import Static, RichLog
from textual.containers import Vertical
from rich.text import Text


_ICONOS_TOOL = {
    "text": "💬", "web": "🔍", "shell": "🖥️", "launch": "🚀",
    "vision": "👁️", "codigo": "💻", "memory": "🧩", "file_write": "💾",
}

# Prefijos conocidos de los print() en graph_nodes.py → color rich
_ESTILO_LOG = (
    ("❌", "bold red"),
    ("⚠️", "bold yellow"),
    ("🔮", "bold magenta"),
    ("🔧", "bold cyan"),
    ("✅", "bold green"),
)


class PlanPanel(Vertical):
    """Panel lateral: lista de pasos del plan + estado de cada uno."""

    # Nota: sin reactive() con always_update aquí — esos watchers se disparaban
    # antes de que compose() montara #plan-body, causando un render con
    # visual=None dentro del propio Vertical. Mantenemos el estado como
    # atributos simples y refrescamos manualmente vía _repintar().

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan_pasos: list[dict] = []
        self.plan_index: int = 0
        self.plan_activo: bool = False

    def compose(self):
        yield Static("[b]PLAN[/b]", id="plan-title")
        yield Static("Sin plan activo.", id="plan-body")

    def on_mount(self) -> None:
        self._repintar()

    def actualizar(self, plan_pasos: list[dict], plan_index: int, plan_activo: bool) -> None:
        self.plan_pasos = plan_pasos
        self.plan_index = plan_index
        self.plan_activo = plan_activo
        self._repintar()

    def marcar_simple(self, tool: str) -> None:
        """Caso plan de 1 paso (no multi-tool): mostrarlo igual, sin lista."""
        icono = _ICONOS_TOOL.get(tool, "•")
        body = self.query_one("#plan-body", Static)
        body.update(f"{icono} Tarea simple → [b]{tool}[/b]")

    def _repintar(self) -> None:
        body = self.query_one("#plan-body", Static)
        if not self.plan_pasos:
            body.update("Sin plan activo." if not self.plan_activo else "Planificando...")
            return

        lineas = []
        for i, paso in enumerate(self.plan_pasos):
            tool = paso.get("tool", "?") if isinstance(paso, dict) else "?"
            icono = _ICONOS_TOOL.get(tool, "•")
            instruccion = ""
            if isinstance(paso, dict):
                instruccion = (paso.get("instruccion") or "")[:40]

            if i < self.plan_index:
                marcador, estilo = "✅", "dim"
            elif i == self.plan_index and self.plan_activo:
                marcador, estilo = "▶", "bold yellow"
            else:
                marcador, estilo = "○", "dim"

            linea = f"[{estilo}]{marcador} {icono} {i + 1}. {tool}[/{estilo}]"
            if instruccion:
                linea += f"\n   [dim]{instruccion}[/dim]"
            lineas.append(linea)

        body.update("\n".join(lineas))

    def reset(self) -> None:
        self.plan_pasos = []
        self.plan_index = 0
        self.plan_activo = False
        self._repintar()


class ProgressLog(RichLog):
    """
    Log de progreso: una línea por cada print() capturado de stdout
    (graph_nodes.py ya los emite, solo los coloreamos por prefijo).
    """

    def __init__(self, **kwargs):
        super().__init__(wrap=True, markup=False, highlight=False, **kwargs)

    def agregar_linea(self, texto: str) -> None:
        estilo = None
        for prefijo, est in _ESTILO_LOG:
            if prefijo in texto:
                estilo = est
                break
        if estilo:
            self.write(Text(texto, style=estilo))
        else:
            self.write(Text(texto, style="dim"))