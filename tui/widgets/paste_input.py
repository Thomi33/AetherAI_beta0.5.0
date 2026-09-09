"""
paste_input.py — Input widget con soporte de "pegado colapsado".

Bug real encontrado: el widget Input base de Textual (ver
_on_paste en textual/widgets/_input.py) es de una sola línea y, al
recibir un paste, hace `event.text.splitlines()[0]` — es decir, toma
SOLO la primera línea del texto pegado y descarta el resto en
silencio. Por eso pegar código o texto con saltos de línea "no
funcionaba": en realidad se pegaba, pero solo la primera línea, sin
ningún aviso.

PasteInput intercepta el evento Paste antes de que el Input base lo
procese. Si el texto pegado es multilínea o supera un umbral de
caracteres, lo colapsa en el visual a un placeholder tipo
"[pasted 842 characters]" (al estilo Claude Code / otros agentes CLI)
y guarda el texto real aparte. Al enviar el mensaje, resolver_texto()
sustituye cada placeholder por su contenido real antes de mandarlo al
motor.

Ctrl+R (con el cursor sobre o dentro de un placeholder, o si solo hay
un pegado activo) abre PastePreviewScreen para ver el contenido
completo sin necesidad de expandirlo en la línea de input (que sigue
siendo de una sola línea).
"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.widgets import Input

PASTE_COLLAPSE_THRESHOLD = 200  # caracteres a partir de los cuales se colapsa


class PasteInput(Input):
    """Input de una línea que colapsa pegados largos/multilínea en un placeholder."""

    BINDINGS = [
        Binding("ctrl+r", "ver_pegado", "ver pegado completo", show=False),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pastes: dict[str, str] = {}  # placeholder visible -> texto real

    def _on_paste(self, event: events.Paste) -> None:
        text = event.text or ""
        if not text:
            event.stop()
            return

        es_multilinea = "\n" in text or "\r" in text
        es_largo = len(text) > PASTE_COLLAPSE_THRESHOLD

        event.stop()
        selection = self.selection

        if not es_multilinea and not es_largo:
            # Pegado corto de una sola línea: comportamiento normal de Input.
            if selection.is_empty:
                self.insert_text_at_cursor(text)
            else:
                self.replace(text, *selection)
            return

        # Pegado largo o multilínea: colapsar a placeholder, guardar el real aparte.
        placeholder = f"[pasted {len(text)} characters]"
        self._pastes[placeholder] = text

        if selection.is_empty:
            self.insert_text_at_cursor(placeholder)
        else:
            self.replace(placeholder, *selection)

    def _placeholder_bajo_cursor(self) -> str | None:
        """Busca si el cursor está dentro de un placeholder de pegado, o si
        hay un único pegado activo (caso común), y devuelve su texto real."""
        if not self._pastes:
            return None

        pos = self.cursor_position
        for placeholder, real in self._pastes.items():
            idx = self.value.find(placeholder)
            if idx != -1 and idx <= pos <= idx + len(placeholder):
                return real

        if len(self._pastes) == 1:
            return next(iter(self._pastes.values()))

        return None

    def action_ver_pegado(self) -> None:
        texto = self._placeholder_bajo_cursor()
        if texto is None:
            return
        from tui.widgets.selector_screens import PastePreviewScreen
        self.app.push_screen(PastePreviewScreen(texto=texto))

    def resolver_texto(self, texto: str) -> str:
        """Sustituye cada placeholder de pegado por el texto real. Se llama
        justo antes de enviar el mensaje al motor (ver app.py:_enviar_mensaje)."""
        for placeholder, real in self._pastes.items():
            texto = texto.replace(placeholder, real)
        return texto

    def limpiar_pegados(self) -> None:
        """Descarta los pegados guardados. Se llama después de cada envío
        para no ir acumulando texto viejo en memoria ni resolver placeholders
        de un mensaje anterior por error."""
        self._pastes.clear()

    def clear(self) -> None:
        super().clear()
        self.limpiar_pegados()
