"""
config.py — Widget ConfigPanel para la TUI de Aether.

Panel interactivo de configuración en caliente sin reiniciar.

Uso:
    from tui.widgets.config import ConfigPanel
    from core.config.config_manager import get_config_manager

    config = ConfigPanel(config_manager=get_config_manager())

    # Añadir al layout de Textual
    yield config
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static, Input
from textual.screen import Screen

from core.config import get_config_manager, ConfigManager


class ConfigOption(Static):
    """Opción individual de configuración con input para editar."""

    def __init__(self, key: str, value: any, config: ConfigManager):
        super().__init__()
        self.key = key
        self.value = value
        self.config = config
        self._editing = False
        self._input_widget: Input | None = None
        self._button_widget: Button | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="config-option-row"):
            yield Label(f"[bold]{self.key}[/]", classes="config-key")
            yield Static(str(self.value), classes="config-value", id=f"value-{self.key}")
            self._button_widget = Button("✎", classes="config-edit-btn", variant="primary", id=f"edit-{self.key}")
            yield self._button_widget

    # ── Wiring real de eventos (fix: antes se asignaba .action, que no existe) ──

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button is self._button_widget:
            event.stop()
            self._start_editing()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._input_widget is not None and event.input is self._input_widget:
            event.stop()
            self._save_editing()

    def _start_editing(self) -> None:
        """Reemplazar value por Input para editar."""
        if self._editing:
            return

        value_widget = self.query_one(f"#value-{self.key}", Static)
        value_widget.display = False

        self._input_widget = Input(
            value=str(self.value),
            placeholder=f"Ingrese nuevo valor para {self.key}",
            classes="config-input",
            id=f"input-{self.key}",
        )

        row = self.query_one(".config-option-row")
        if self._button_widget:
            self._button_widget.display = False

        row.mount(self._input_widget)
        self._input_widget.focus()
        self._editing = True

    def _save_editing(self) -> bool:
        """Guardar el valor ingresado y terminar edición."""
        if not self._input_widget:
            return False

        new_value = self._input_widget.value.strip()
        converted = self._try_convert_value(new_value, self.value)

        if converted is not None:
            success = self.config.set(self.key, converted)
            if success:
                self.value = converted
                value_widget = self.query_one(f"#value-{self.key}", Static)
                value_widget.update(str(self.value))
                value_widget.display = True

                self._input_widget.remove()
                self._input_widget = None
                if self._button_widget:
                    self._button_widget.display = True
                self._editing = False
                return True
            else:
                value_widget = self.query_one(f"#value-{self.key}", Static)
                value_widget.update(f"[red]{new_value}[/] [dim](error validación)[/]")
                value_widget.display = True
        else:
            value_widget = self.query_one(f"#value-{self.key}", Static)
            value_widget.update(f"[red]{new_value}[/] [dim](tipo inválido)[/]")
            value_widget.display = True

        # Cancelar edición tras fallo de conversión/validación
        if self._input_widget:
            self._input_widget.remove()
            self._input_widget = None
        if self._button_widget:
            self._button_widget.display = True
        self._editing = False
        return False

    def _cancel_editing(self) -> None:
        """Cancelar edición sin guardar (Escape) y restaurar el valor original."""
        if self._input_widget:
            self._input_widget.remove()
            self._input_widget = None
        if self._button_widget:
            self._button_widget.display = True

        value_widget = self.query_one(f"#value-{self.key}", Static)
        value_widget.update(str(self.value))
        value_widget.display = True
        self._editing = False

    def _try_convert_value(self, value_str: str, original_value: any) -> any:
        """Intentar convertir string a tipo correcto."""
        if isinstance(original_value, bool):
            lower = value_str.lower()
            if lower in ("true", "1", "yes", "on", "t", "si", "sí", "verdadero"):
                return True
            elif lower in ("false", "0", "no", "off", "f", "falso"):
                return False
            return None

        if isinstance(original_value, int):
            try:
                return int(value_str)
            except ValueError:
                return None

        if isinstance(original_value, float):
            try:
                return float(value_str)
            except ValueError:
                return None

        return value_str

    def on_key(self, event) -> None:
        """Escape cancela la edición (fix: ahora detiene el evento para que
        no siga burbujeando y cierre el modal completo de config)."""
        if self._editing and event.key == "escape":
            event.stop()
            self._cancel_editing()


class ConfigSection(Vertical):
    """Sección de configuración con título."""

    def __init__(self, title: str, options: list[tuple[str, any]], config: ConfigManager):
        super().__init__()
        # Fix: renombrado de self.title -> self.section_title para no pisar
        # atributos propios de la jerarquía de widgets de Textual.
        self.section_title = title
        self.options = options
        self.config = config
        self._option_widgets: list[ConfigOption] = []

    def compose(self) -> ComposeResult:
        yield Label(f"[bold]{self.section_title}[/]", classes="config-section-title")

        for key, value in self.options:
            option = ConfigOption(key, value, self.config)
            self._option_widgets.append(option)
            yield option

    def on_mount(self) -> None:
        """Fix: era _on_mount (guión bajo) y Textual nunca lo llamaba,
        así que la clase 'config-section' no se aplicaba jamás."""
        self.add_class("config-section")


class ConfigPanel(Vertical):
    """
    Panel interactivo de configuración.

    Permite editar configuraciones en caliente sin reiniciar el proceso.
    Muestra una lista de opciones organizadas por categoría.
    """

    def __init__(self, config: ConfigManager = None):
        super().__init__()
        self.config = config or get_config_manager()
        self._sections: list[ConfigSection] = []

    def compose(self) -> ComposeResult:
        yield Static("[bold]⚙️ CONFIGURACIÓN EN CALIENTE[/bold]", classes="config-header")

        # Fix: antes se hacía `yield ConfigSection(...)` directo, sin guardar
        # la referencia — self._sections quedaba vacío para siempre y
        # action_refresh() no tenía nada sobre qué iterar.

        seccion_llm = ConfigSection(
            "🧠 LLM",
            [
                ("MODELO", self.config.get("MODELO")),
                ("MODELO_VISION", self.config.get("MODELO_VISION")),
                ("TEMPERATURE", self.config.get("TEMPERATURE")),
                ("MAX_TOKENS", self.config.get("MAX_TOKENS")),
                ("NUM_CTX", self.config.get("NUM_CTX")),
            ],
            self.config,
        )
        self._sections.append(seccion_llm)
        yield seccion_llm

        seccion_rendimiento = ConfigSection(
            "⚡ Rendimiento",
            [
                ("TIMEOUT_CMD", self.config.get("TIMEOUT_CMD")),
                ("MAX_TURNOS_CONTEXTO", self.config.get("MAX_TURNOS_CONTEXTO")),
                ("MAX_TURNOS_CONTEXTO_CHAT", self.config.get("MAX_TURNOS_CONTEXTO_CHAT")),
                ("REFRESH_RATE", self.config.get("REFRESH_RATE")),
            ],
            self.config,
        )
        self._sections.append(seccion_rendimiento)
        yield seccion_rendimiento

        seccion_modo = ConfigSection(
            "🕹️ Modo",
            [
                ("MODO_AUTONOMO", self.config.get("MODO_AUTONOMO")),
                ("TOOL_CALLING_NATIVO", self.config.get("TOOL_CALLING_NATIVO")),
                ("VERBOSE", self.config.get("VERBOSE")),
                ("DEBUG", self.config.get("DEBUG")),
            ],
            self.config,
        )
        self._sections.append(seccion_modo)
        yield seccion_modo

        seccion_ui = ConfigSection(
            "🎨 UI",
            [
                ("THEME", self.config.get("THEME")),
            ],
            self.config,
        )
        self._sections.append(seccion_ui)
        yield seccion_ui

    def on_mount(self) -> None:
        self.add_class("config-panel")

    def action_close(self) -> None:
        """Cerrar el panel."""
        self.remove()

    def action_refresh(self) -> None:
        """Refrescar configuración desde ConfigManager.

        Fix: self._sections ahora sí tiene contenido (ver compose()), así
        que este loop realmente actualiza los valores en pantalla.
        """
        self.config.reload()

        for section in self._sections:
            for option in section._option_widgets:
                option.value = self.config.get(option.key)
                option.query_one(".config-value", Static).update(str(option.value))

        self.query_one(".config-header", Static).update(
            "[bold]⚙️ CONFIGURACIÓN REFRESCADA ✓[/bold]"
        )


class ConfigScreen(Screen):
    """Pantalla modal para configuración."""

    # Fix: faltaba el binding de Escape — el texto de ayuda vieja de app.py
    # prometía "Esc para salir" pero nunca estaba conectado a nada acá.
    BINDINGS = [
        ("escape", "close", "Cerrar"),
    ]

    DEFAULT_CSS = """
    ConfigScreen {
        layout: vertical;
        align: center middle;
    }

    #config-container {
        width: 80%;
        height: 80%;
        border: thick $border;
        background: $surface;
        padding: 1;
    }

    .config-header {
        margin: 0 0 1 0;
        text-align: center;
        padding: 1;
        background: $panel;
        color: $text;
        border-bottom: solid $accent;
    }

    .config-section-title {
        margin: 1 0 0 0;
        padding: 0 0 0 1;
        border-left: solid $accent;
        color: $text;
    }

    .config-option-row {
        layout: horizontal;
        width: 100%;
        margin: 0 0 0 1;
    }

    .config-key {
        width: 30%;
        color: $accent;
    }

    .config-value {
        width: 50%;
        color: $text;
    }

    .config-edit-btn {
        width: 8;
        margin: 0 0 0 1;
    }

    .config-input {
        width: 50%;
    }

    .config-section {
        margin: 1 0 1 0;
        padding: 0;
        border-bottom: dashed $panel;
    }

    Button {
        margin: 0 0 1 0;
        width: 100%;
    }

    #config-footer {
        dock: bottom;
        margin: 1 0 0 0;
        height: 3;
    }
    """

    def compose(self) -> ComposeResult:
        yield ConfigPanel(get_config_manager())

        with Horizontal(id="config-footer"):
            yield Button("✓ Guardar y Cerrar", variant="success", id="save-close")
            yield Button("✗ Cancelar", variant="error", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-close":
            self.action_save_and_close()
        elif event.button.id == "cancel":
            self.app.pop_screen()

    def action_save_and_close(self) -> None:
        """Guardar configuración y cerrar.

        Fix: antes no hacía nada más que pop_screen(), confiando en que
        cada edición individual ya haya persistido vía config.set(). Se
        agrega un intento defensivo de config.save() por si ConfigManager
        tiene un paso de persistencia a disco separado (p. ej. escribir a
        un .env o json). Si no existe el método, no rompe nada.
        """
        config = get_config_manager()
        if hasattr(config, "save") and callable(config.save):
            config.save()
        self.app.pop_screen()

    def action_close(self) -> None:
        """Cerrar sin guardar (Escape)."""
        self.app.pop_screen()