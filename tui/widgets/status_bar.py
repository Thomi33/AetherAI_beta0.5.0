"""
Widget Status Bar para mostrar información del modelo y estado del sistema en la TUI.
Muestra modelos, contexto usado, temperatura, etc.
"""


from textual.widgets import Static, Label


class StatusBar(Static):
    """Barra de estado que muestra información relevante del sistema."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model = "ornith:9b"
        self._provider = "Ollama"
        self._thinking = False
        self._tools_enabled = True
        self._context_used = 0
        self._context_total = 128000

    def actualizar(self, model=None, provider=None, thinking=None, tools_enabled=None, context_used=None, context_total=None):
        """Actualiza los datos de la barra de estado."""
        if model is not None:
            self._model = model
        if provider is not None:
            self._provider = provider
        if thinking is not None:
            self._thinking = thinking
        if tools_enabled is not None:
            self._tools_enabled = tools_enabled
        if context_used is not None:
            self._context_used = context_used
        if context_total is not None:
            self._context_total = context_total

    def _repintar(self):
        """Reconstruye el contenido visual de la barra."""
        thinking_str = "ON" if self._thinking else "OFF"
        tools_str = "on" if self._tools_enabled else "off"
        
        texto = (
            f"{self._model}  ·  thinking:{thinking_str}  ·  tools:{tools_str}  ·  "
            f"context: {self._context_used // 1000}k/{self._context_total // 1000}k"
        )

        self.update(texto)

    def reset(self):
        """Resetea la barra a su estado inicial."""
        self._model = "ornith:9b"
        self._provider = "Ollama"
        self._thinking = False
        self._tools_enabled = True
        self._context_used = 0
        self._context_total = 128000
        self._repintar()