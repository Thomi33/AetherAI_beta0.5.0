"""
app.py — Aplicación principal TUI de Aether.

Rediseño completo: estructura modular con widgets especializados para chat, plan, estado y debug.
Usa RichLog para el historial de chat y RichLogList para logs de herramientas/debug.
"""


from textual.app import App, ComposeResult
from textual.widgets import (
    Header, Footer, Static, Label, RichLog,
    Input, Button, TabbedContent, Tabs,
)

# Importamos los widgets personalizados
from tui.widgets.plan_panel import PlanPanel
from tui.widgets.status_bar import StatusBar
from tui.widgets.debug_panel import DebugPanel


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

    def compose(self) -> ComposeResult:
        """Compone la interfaz con los widgets principales."""
        yield Header()
        yield RichLog(id="chat_log", auto_scroll=True, classes="chat-log")
        yield Input(placeholder="Escribí tu mensaje aquí...", id="input_chat")
        yield Button("Enviar", id="btn_enviar")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed):
        """Maneja el evento de presionar el botón de enviar."""
        if event.button.id == "btn_enviar":
            self._enviar_mensaje()

    def _enviar_mensaje(self):
        """Envía un mensaje al chat y lo procesa en el motor."""
        input_widget = self.query_one("#input_chat", Input)
        texto = input_widget.value.strip()
        if texto:
            # Agrega el mensaje del usuario al chat
            chat_log = self.query_one("#chat_log", RichLog)
            chat_log.write(f"👤 {texto}")

            # Aquí iría la lógica de enviar al motor y manejar la respuesta
            input_widget.value = ""

    def on_key(self, event):
        """Maneja eventos de teclado."""
        if event.key == "enter":
            self._enviar_mensaje()

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
