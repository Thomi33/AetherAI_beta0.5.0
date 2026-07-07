"""
app.py — Aplicación principal TUI de Aether.
"""

from textual.app import App, ComposeResult
from textual.widgets import (
    Header, Footer, Static, Label, RichLog,
    Input, Button, TabbedContent, Tabs,
)
from textual import work

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
    Screen {
        background: $surface;
        color: $text;
    }

    #chat_log {
        height: 1fr;
    }

    #streaming_line {
        height: auto;
        color: $secondary-lighten-2;
        padding: 0 1;
    }

    #plan_panel, #status_bar, #debug_panel {
        dock: bottom;
    }

    #status_bar {
        height: 2;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #debug_panel {
        height: 4;
        background: $surface;
        color: $text;
        padding: 0 1;
    }

    .chat-line {
        margin-bottom: 1;
    }

    .user-message {
        color: $primary-lighten-2;
    }

    .assistant-message {
        color: $secondary-lighten-2;
    }

    #plan_panel {
        height: 6;
        background: $surface-darken-1;
        color: $text;
        padding: 0 1;
    }

    #tools_log {
        height: 4;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._plan_pasos = []
        self._plan_index = 0
        self._plan_activo = False
        self._model = "ornith:9b"
        self._thinking = False
        self._tools_enabled = True
        self._context_used = 0
        self._respuesta_en_curso = ""  # buffer de tokens del turno actual

    def compose(self) -> ComposeResult:
        """Compone la interfaz con los widgets principales."""
        yield Header()
        yield RichLog(id="chat_log", auto_scroll=True, markup=True, classes="chat-log")
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
                self.query_one("#chat_log", RichLog).write,
                f"⚠️ Error inicializando el motor: {e}",
            )

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_enviar":
            self._enviar_mensaje()

    def on_key(self, event):
        if event.key == "enter" and self.focused and self.focused.id == "input_chat":
            self._enviar_mensaje()

    def _enviar_mensaje(self):
        """Envía un mensaje al chat y dispara el procesamiento en el motor."""
        if self._thinking:
            return  # ya hay un turno en curso

        input_widget = self.query_one("#input_chat", Input)
        texto = input_widget.value.strip()
        if not texto:
            return

        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.write(f"👤 {texto}")
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
        """Se ejecuta siempre en el hilo principal (thread-safe para widgets)."""
        chat_log = self.query_one("#chat_log", RichLog)
        streaming_line = self.query_one("#streaming_line", Static)

        if isinstance(evento, TokenEvent):
            # Acumulamos en el buffer y actualizamos SIEMPRE el mismo widget
            # en vez de escribir una línea nueva por token -> no más saltos de línea.
            self._respuesta_en_curso += evento.fragmento
            streaming_line.update(f"🎙️  {self._respuesta_en_curso}")

        elif isinstance(evento, StdoutLineEvent):
            debug_panel = self.query_one("#debug_panel", DebugPanel)
            debug_panel.agregar_log(evento.texto)

        elif isinstance(evento, NodeUpdateEvent):
            plan_panel = self.query_one("#plan_panel", PlanPanel)
            if hasattr(plan_panel, "actualizar_delta"):
                plan_panel.actualizar_delta(evento.nodo, evento.delta)

        elif isinstance(evento, DoneEvent):
            # Volcamos la respuesta completa como UNA sola línea al chat_log
            # y limpiamos el widget de streaming.
            respuesta_final = self._respuesta_en_curso.strip() or evento.respuesta
            chat_log.write(f"🎙️  Aether: {respuesta_final}")
            streaming_line.update("")
            self._finalizar_turno()

        elif isinstance(evento, ErrorEvent):
            chat_log.write(f"⚠️ Error: {evento.mensaje}")
            streaming_line.update("")
            self._finalizar_turno()

    def _finalizar_turno(self) -> None:
        self._thinking = False
        self._respuesta_en_curso = ""
        input_widget = self.query_one("#input_chat", Input)
        input_widget.disabled = False
        input_widget.focus()

    @property
    def plan_pasos(self) -> list:
        return self._plan_pasos

    @property
    def plan_index(self) -> int:
        return self._plan_index

    @property
    def plan_activo(self) -> bool:
        return self._plan_activo


def run() -> None:
    """Punto de entrada para lanzar la TUI de Aether."""
    app = AetherApp()
    app.run()