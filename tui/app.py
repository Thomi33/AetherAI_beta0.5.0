"""
app.py — TUI principal de Aether (Textual), estilo Claude Code / OpenCode.

Layout:
    ┌──────────────────────────────────────────────────────┐
    │ 🤖 AETHER                    ● listo / ⚙ procesando   │  Header (reactivo)
    ├───────────────────┬────────────────────────────────────┤
    │  PLAN              │  Chat (RichLog, scrollable)        │
    │  (PlanPanel)        │                                    │
    │  ──────────────     │                                    │
    │  LOG               │                                    │
    │  (ProgressLog)      │                                    │
    ├───────────────────┴────────────────────────────────────┤
    │ > input                                                 │  Input
    └──────────────────────────────────────────────────────┘

No toca graph_nodes.py (Opción A). Todo el progreso llega vía
engine_bridge.iter_eventos(), corrido en un worker thread para no bloquear
la UI mientras Ollama/LangGraph procesan.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Input, RichLog, Static
from textual.worker import Worker, WorkerState
from rich.text import Text

from .widgets import PlanPanel, ProgressLog
from . import engine_bridge as bridge


PALABRAS_SALIDA = {"salir", "adios", "exit", "apágate", "apagate", "quit"}


class AetherApp(App):
    """App principal de la TUI de Aether."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #cuerpo {
        height: 1fr;
    }

    #sidebar {
        width: 32;
        border: round $accent;
        padding: 0 1;
    }

    #plan-panel {
        height: auto;
        max-height: 50%;
        border-bottom: solid $accent;
        padding-bottom: 1;
    }

    #progress-log {
        height: 1fr;
        background: $surface;
    }

    #chat-wrapper {
        width: 1fr;
        border: round $accent;
    }

    #chat-log {
        height: 1fr;
        padding: 0 1;
    }

    #chat-streaming {
        height: auto;
        padding: 0 1;
        color: $success;
    }

    #estado-bar {
        dock: top;
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }

    Input {
        dock: bottom;
    }
    """

    BINDINGS = [
        ("ctrl+c", "salir_app", "Salir"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._procesando = False
        self._buffer_streaming = ""

    # ──────────────────────────────────────────────────────────────
    # LAYOUT
    # ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield RichLog(id="estado-bar", markup=True, wrap=False, highlight=False)
            with Horizontal(id="cuerpo"):
                with Vertical(id="sidebar"):
                    yield PlanPanel(id="plan-panel")
                    yield ProgressLog(id="progress-log")
                with Vertical(id="chat-wrapper"):
                    yield RichLog(id="chat-log", markup=True, wrap=True)
                    yield Static("", id="chat-streaming", markup=False)
        yield Input(placeholder="Escribí una orden para Aether...", id="input-orden")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "AETHER"
        self.sub_title = "Agente local — LangGraph"
        self._set_estado("inicializando", "⏳")
        self.query_one("#chat-log", RichLog).write(
            Text("🤖 AETHER — Agente Local Inteligente (TUI)", style="bold cyan")
        )
        self.run_worker(self._inicializar_motor, thread=True, exclusive=True, name="init")

    # ──────────────────────────────────────────────────────────────
    # ESTADO / HEADER
    # ──────────────────────────────────────────────────────────────

    def _set_estado(self, texto: str, icono: str = "●") -> None:
        barra = self.query_one("#estado-bar", RichLog)
        barra.clear()
        color = "yellow" if self._procesando else "green"
        barra.write(Text(f"{icono} {texto}", style=f"bold {color}"))

    # ──────────────────────────────────────────────────────────────
    # INICIALIZACIÓN (en worker thread, no bloquea la UI)
    # ──────────────────────────────────────────────────────────────

    def _inicializar_motor(self) -> None:
        try:
            bridge.inicializar_motor()
            self.call_from_thread(self._on_motor_listo)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._on_motor_error, str(e))

    def _on_motor_listo(self) -> None:
        self._set_estado("listo", "●")
        self.query_one("#chat-log", RichLog).write(
            Text("✅ Aether listo. Escribí tu orden abajo.", style="green")
        )
        self.query_one(Input).focus()

    def _on_motor_error(self, mensaje: str) -> None:
        self._set_estado(f"error de inicialización: {mensaje}", "❌")

    # ──────────────────────────────────────────────────────────────
    # INPUT DEL USUARIO
    # ──────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        orden = event.value.strip()
        event.input.value = ""
        if not orden:
            return

        if orden.lower() in PALABRAS_SALIDA:
            self.exit()
            return

        if self._procesando:
            self.query_one("#chat-log", RichLog).write(
                Text("⏳ Todavía estoy procesando la orden anterior...", style="yellow")
            )
            return

        chat = self.query_one("#chat-log", RichLog)
        chat.write(Text(f"\n🧠 Tú: {orden}", style="bold white"))

        self.query_one(PlanPanel).reset()
        self.query_one(ProgressLog).clear()
        self._buffer_streaming = ""
        self.query_one("#chat-streaming", Static).update("")

        self._procesando = True
        self._set_estado(f"procesando: {orden[:40]}", "⚙")

        self.run_worker(
            lambda: self._procesar_orden(orden),
            thread=True,
            exclusive=False,
            name="orden",
        )

    # ──────────────────────────────────────────────────────────────
    # PROCESAMIENTO (worker thread) — consume engine_bridge.iter_eventos
    # ──────────────────────────────────────────────────────────────

    def _procesar_orden(self, orden: str) -> None:
        for evento in bridge.iter_eventos(orden):
            self.call_from_thread(self._manejar_evento, evento)

    def _manejar_evento(self, evento: bridge.Evento) -> None:
        plan_panel = self.query_one(PlanPanel)
        progress_log = self.query_one(ProgressLog)
        chat = self.query_one("#chat-log", RichLog)

        if isinstance(evento, bridge.NodeUpdateEvent):
            delta = evento.delta

            # El planner dejó plan_pasos en el estado → pintamos el panel.
            if "plan_pasos" in delta or "plan_activo" in delta:
                plan_panel.actualizar(
                    plan_pasos=delta.get("plan_pasos", plan_panel.plan_pasos),
                    plan_index=delta.get("plan_index", plan_panel.plan_index),
                    plan_activo=delta.get("plan_activo", plan_panel.plan_activo),
                )
            elif "plan_index" in delta:
                plan_panel.plan_index = delta["plan_index"]
                plan_panel._repintar()

            if delta.get("error_activo"):
                progress_log.agregar_linea(
                    f"⚠️ [{evento.nodo}] {delta.get('error_mensaje', 'error')}"
                )

        elif isinstance(evento, bridge.StdoutLineEvent):
            texto = evento.texto
            if "🎙️" in texto:
                # node_vision y node_memory hacen print(f"🎙️  Aether: {resp}")
                # — línea completa, sin streaming token a token. Va al chat,
                # no al sidebar log. Quitamos el prefijo para mostrarlo igual
                # que las respuestas de texto.
                contenido = texto.split("🎙️", 1)[-1].lstrip(" \t:").strip()
                if contenido:
                    chat.write(Text(f"\n🎙️  Aether: {contenido}", style="bold green"))
            else:
                # Líneas de progreso normales (🔧, 🔮, 🔍, etc.) → sidebar log
                progress_log.agregar_linea(texto)

        elif isinstance(evento, bridge.TokenEvent):
            # Streaming token a token de _llm_chat → lo vamos acumulando
            # y mostrando en vivo en el widget de "respuesta en curso".
            self._buffer_streaming += evento.fragmento
            streaming_widget = self.query_one("#chat-streaming", Static)
            streaming_widget.update(self._buffer_streaming)

        elif isinstance(evento, bridge.DoneEvent):
            # Si hubo streaming en vivo, lo volcamos como línea fija del
            # chat y limpiamos el widget temporal. _on_token en
            # graph_nodes.py ya inyecta "🎙️  Aether: " como parte del
            # primer chunk — lo recortamos acá para no duplicarlo.
            texto_final = self._buffer_streaming.strip() or evento.respuesta
            if texto_final.startswith("🎙️"):
                texto_final = texto_final.split(":", 1)[-1].strip()
            chat.write(Text(f"\n🎙️  Aether: {texto_final}", style="bold green"))
            self.query_one("#chat-streaming", Static).update("")
            self._buffer_streaming = ""
            self._procesando = False
            self._set_estado("listo", "●")

        elif isinstance(evento, bridge.ErrorEvent):
            chat.write(Text(f"\n❌ Error: {evento.mensaje}", style="bold red"))
            self.query_one("#chat-streaming", Static).update("")
            self._buffer_streaming = ""
            self._procesando = False
            self._set_estado("listo (con error previo)", "●")

    # ──────────────────────────────────────────────────────────────
    # ACCIONES
    # ──────────────────────────────────────────────────────────────

    def action_salir_app(self) -> None:
        self.exit()


def run() -> None:
    AetherApp().run()