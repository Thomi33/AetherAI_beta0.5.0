"""
chat_panel.py — Widget para mostrar conversaciones entre usuario y Aether.
"""


from textual.widgets import RichLog, Static, Label


class ChatPanel(Static):
    """Widget que muestra la conversación del chat."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._chat_log = None  # Se inicializa con el RichLog real en compose()

    @property
    def log(self):
        return self._chat_log

    @log.setter
    def log(self, value):
        self._chat_log = value

    def agregar_mensaje(self, mensaje=None, tipo="user"):
        """Agrega un mensaje al chat. Se llama desde app.py."""
        if mensaje is None:
            return
        
        prefix = "👤 " if tipo == "user" else "🎙️  Aether: "
        texto = f"{prefix}{mensaje}"
        
        # Usamos el RichLog real si está disponible
        if self._chat_log:
            self._chat_log.append(texto)
        else:
            # Fallback: agregar directamente al widget padre (Static)
            self.update(f"\n{texto}")

    def reset(self):
        """Resetea el chat."""
        if self._chat_log:
            self._chat_log.clear()