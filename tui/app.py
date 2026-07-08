"""
app.py — Aplicación principal TUI de Aether.
"""

from rich.markup import escape

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Input, Button
from textual import work

from tui.widgets.chat_panel import ChatPanel
from tui.widgets.plan_panel import PlanPanel
from tui.widgets.status_bar import StatusBar
from tui.widgets.debug_panel import DebugPanel

from tui.engine_bridge import (
    iter_eventos,
    inicializar_motor,
    NodeUpdateEvent,
    StdoutLineEvent,
    TokenEvent,
    DoneEvent,
    ErrorEvent,
)


class AetherApp(App):
    """Aplicación TUI principal de Aether."""

    CSS = """
    Screen { background: $surface; color: $text; }
    #chat_panel { height: 1fr; }
    #streaming_line { height: auto; color: $secondary-lighten-2; padding: 0 1; }
    #plan_panel, #status_bar, #debug_panel { dock: bottom; }
    #status_bar { height: 2; background: $primary; color: $text; padding: 0 1; }
    #debug_panel { height: 4; background: $surface; color: $text; padding: 0 1; }
    #plan_panel { height: 6; background: $surface-darken-1; color: $text; padding: 0 1; }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._thinking = False
        self._respuesta_en_curso = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield ChatPanel(id="chat_panel")
        yield Static("", id="streaming_line")
        yield PlanPanel(id="plan_panel")
        yield DebugPanel(id="debug_panel")
        yield StatusBar(id="status_bar")
        yield Input(placeholder="Escribí tu mensaje aquí...", id="input_chat")
        yield Button("Enviar", id="btn_enviar")
        yield Footer()

    def on_mount(self) -> None:
        self._inicializar_motor_bg()

    @work(thread=True)
    def _inicializar_motor_bg(self) -> None:
        try:
            inicializar_motor()
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.query_one("#chat_panel", ChatPanel).agregar_mensaje,
                f"⚠️ Error inicializando el motor: {e}",
                "assistant",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_enviar":
            self._enviar_mensaje()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input_chat":
            event.stop()
            self._enviar_mensaje()

    def _enviar_mensaje(self) -> None:
        if self._thinking:
            return

        input_widget = self.query_one("#input_chat", Input)
        texto = input_widget.value.strip()
        if not texto:
            return

        chat_panel = self.query_one("#chat_panel", ChatPanel)
        chat_panel.agregar_mensaje(texto, "user")
        input_widget.value = ""

        self._thinking = True
        self._respuesta_en_curso = ""
        input_widget.disabled = True
        self._procesar_orden_bg(texto)

    @work(thread=True, exclusive=True)
    def _procesar_orden_bg(self, orden: str) -> None:
        for evento in iter_eventos(orden):
            self.call_from_thread(self._manejar_evento, evento)

    def _manejar_evento(self, evento) -> None:
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        streaming_line = self.query_one("#streaming_line", Static)

        if isinstance(evento, TokenEvent):
            self._respuesta_en_curso += evento.fragmento
            streaming_line.update(f"🎙️  {escape(self._respuesta_en_curso)}")

        elif isinstance(evento, StdoutLineEvent):
            self.query_one("#debug_panel", DebugPanel).agregar_log(evento.texto)

        elif isinstance(evento, NodeUpdateEvent):
            plan_panel = self.query_one("#plan_panel", PlanPanel)
            if hasattr(plan_panel, "actualizar_delta"):
                plan_panel.actualizar_delta(evento.nodo, evento.delta)

        elif isinstance(evento, DoneEvent):
            respuesta_final = self._respuesta_en_curso.strip() or evento.respuesta
            chat_panel.agregar_mensaje(respuesta_final, "assistant")
            streaming_line.update("")
            self._finalizar_turno()

        elif isinstance(evento, ErrorEvent):
            chat_panel.agregar_mensaje(f"⚠️ Error: {evento.mensaje}", "assistant")
            streaming_line.update("")
            self._finalizar_turno()

    def _finalizar_turno(self) -> None:
        self._thinking = False
        self._respuesta_en_curso = ""
        input_widget = self.query_one("#input_chat", Input)
        input_widget.disabled = False
        input_widget.focus()


def run() -> None:
    app = AetherApp()
    app.run()