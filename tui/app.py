"""
app.py — TUI principal de Aether (Textual).

Rediseño v2 (estilo Claude Code / OpenCode):
- Un solo acento de color; todo lo demás en gris/blanco sobre negro puro.
- Header de una sola línea, sin bordes pesados.
- Chat es el único protagonista: transcript continuo con marcadores tipográficos
  unificados (›, ●, ⎿, ✗) en vez de emojis mezclados con colores random.
- Sidebar (Plan/Tools) oculta por defecto, se muestra con Ctrl+B.
- F2 abre ConfigScreen (modal real, ya implementado en widgets/config.py) en vez
  de volcar texto con markup roto al chat log.

Todo el progreso sigue llegando por engine_bridge.iter_eventos() (bridge intacto).
"""

from __future__ import annotations

import re

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Input, RichLog, Static
from rich.text import Text

from .widgets import PlanPanel
from .widgets.config import ConfigScreen
from . import engine_bridge as bridge
from core.config.config_manager import get_config_manager


PALABRAS_SALIDA = {"salir", "adios", "exit", "apágate", "apagate", "quit"}


class AetherApp(App):
    """App principal de la TUI de Aether."""

    # Un solo acento (definido por el theme de Textual vía $accent).
    # Todo lo demás usa $text / $text-muted — nada de rainbow de colores.
    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #top-bar {
        dock: top;
        height: 1;
        background: $background;
        color: $text-muted;
        padding: 0 1;
    }

    #top-bar .accent {
        color: $accent;
    }

    #cuerpo {
        height: 1fr;
    }

    /* Sidebar: oculta por defecto (Ctrl+B). Sin panel pesado, solo un borde
       izquierdo fino para separar del chat cuando está visible. */
    #sidebar {
        width: 26;
        border-left: solid $panel-lighten-1;
        padding: 0 1;
        background: $background;
        display: none;
        color: $text-muted;
    }

    #sidebar.visible {
        display: block;
    }

    #plan-panel {
        height: auto;
        max-height: 60%;
    }

    #chat-wrapper {
        width: 1fr;
    }

    #chat-log {
        height: 1fr;
        padding: 0 2;
        background: $background;
    }

    #chat-streaming {
        height: auto;
        padding: 0 2 1 2;
        color: $text-muted;
    }

    #bottom-bar {
        dock: bottom;
        height: 1;
        background: $background;
        color: $text-muted;
        padding: 0 1;
    }

    Input {
        dock: bottom;
        background: $background;
        border: none;
        border-top: solid $panel-lighten-1;
        padding: 0 1;
    }

    Input:focus {
        border-top: solid $accent;
    }

    Footer {
        background: $background;
    }
    """

    BINDINGS = [
        ("ctrl+c", "salir_app", "Salir"),
        ("f2", "open_settings", "Settings"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
        ("ctrl+l", "clear_chat", "Clear"),
        ("ctrl+r", "retry_last", "Retry"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._procesando = False
        self._buffer_streaming = ""
        self._sidebar_visible = False  # oculta por defecto — se activa con Ctrl+B
        self._context_used = 0
        self._context_total = 128000
        self._ultima_orden = ""
        self._en_debug_block = False
        self._settings = {
            "model": "ornith:9b",
            "provider": "Ollama",
            "thinking": "ON",
            "reasoning_level": "Balanced",
            "temp": 0.6,
            "max_tokens": 2048,
            "tools_enabled": True,
        }

    # ──────────────────────────────────────────────────────────────
    # LAYOUT
    # ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="top-bar", markup=True)

        with Horizontal(id="cuerpo"):
            with Vertical(id="sidebar"):
                yield PlanPanel(id="plan-panel")
                yield Static("", id="tools-preview", markup=True)

            with Vertical(id="chat-wrapper"):
                yield RichLog(id="chat-log", markup=True, wrap=True)
                yield Static("", id="chat-streaming", markup=False)

        yield Static("", id="bottom-bar", markup=True)
        yield Input(
            placeholder="¿Qué querés que haga?  (F2 Settings · Ctrl+B Sidebar · Ctrl+L Clear)",
            id="input-orden",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = "AETHER"
        self.sub_title = "Agente local · Ornith + LangGraph"
        self._refresh_top_bar()
        self._refresh_bottom_bar()
        self._refresh_tools_preview()

        chat = self.query_one("#chat-log", RichLog)
        chat.write(Text("Aether — listo para trabajar", style="bold"))
        chat.write(Text("Escribí cualquier cosa abajo. Uso herramientas cuando hace falta.", style="dim"))

        self.run_worker(self._inicializar_motor, thread=True, exclusive=True, name="init")

    # ──────────────────────────────────────────────────────────────
    # HEADER / BOTTOM BAR (una sola línea cada uno, sin ruido)
    # ──────────────────────────────────────────────────────────────

    def _refresh_top_bar(self) -> None:
        bar = self.query_one("#top-bar", Static)
        s = self._settings
        ctx_pct = int((self._context_used / self._context_total) * 100) if self._context_total else 0
        thinking = "on" if s["thinking"] == "ON" else "off"
        tools = "on" if s["tools_enabled"] else "off"
        text = (
            f"[accent]aether[/]  ·  [accent]{s['model']}[/]  ·  "
            f"thinking:{thinking}  ·  tools:{tools}  ·  "
            f"ctx {self._context_used // 1000}k/{self._context_total // 1000}k ({ctx_pct}%)"
        )
        bar.update(text)

    def _refresh_bottom_bar(self) -> None:
        bar = self.query_one("#bottom-bar", Static)
        ctx_pct = int((self._context_used / self._context_total) * 100) if self._context_total else 0
        bar_len = 16
        filled = int(bar_len * ctx_pct / 100)
        bar_vis = "█" * filled + "░" * (bar_len - filled)
        bar.update(f"{bar_vis} {ctx_pct}%")

    def _refresh_tools_preview(self) -> None:
        preview = self.query_one("#tools-preview", Static)
        preview.update("tools   shell · web · launch\nmcp     0/0")

    def _apply_config_to_settings(self) -> None:
        """Sincronizar ConfigManager con el estado interno de la TUI (header)."""
        config = get_config_manager()
        self._settings["model"] = config.get("MODELO", "ornith:9b")
        self._settings["provider"] = "Ollama"
        self._settings["thinking"] = "ON" if config.get("MODO_AUTONOMO", True) else "OFF"
        self._settings["temp"] = config.get("TEMPERATURE", 0.6)
        self._settings["max_tokens"] = config.get("MAX_TOKENS", 2048)
        self._settings["tools_enabled"] = config.get("TOOL_CALLING_NATIVO", True)
        self._settings["verbose"] = config.get("VERBOSE", False)
        self._settings["debug"] = config.get("DEBUG", False)

    def _set_estado(self, texto: str, icono: str = "") -> None:
        # Sin panel de estado separado: el header ya refleja todo.
        self._refresh_top_bar()

    # ──────────────────────────────────────────────────────────────
    # INICIALIZACIÓN (worker thread, no bloquea la UI)
    # ──────────────────────────────────────────────────────────────

    def _inicializar_motor(self) -> None:
        try:
            bridge.inicializar_motor()
            self.call_from_thread(self._on_motor_listo)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._on_motor_error, str(e))

    def _on_motor_listo(self) -> None:
        self._apply_config_to_settings()
        self._refresh_top_bar()
        self._refresh_bottom_bar()
        if self._sidebar_visible:
            self.query_one("#sidebar").add_class("visible")
        self.query_one("#chat-log", RichLog).write(Text("listo.", style="dim"))
        self.query_one(Input).focus()

    def _on_motor_error(self, mensaje: str) -> None:
        self.query_one("#chat-log", RichLog).write(Text(f"✗ Error de inicialización: {mensaje}", style="bold red"))

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
            self.query_one("#chat-log", RichLog).write(Text("todavía estoy procesando la orden anterior...", style="dim"))
            return

        chat = self.query_one("#chat-log", RichLog)
        chat.write(Text(f"\n› {orden}", style="bold"))

        self.query_one(PlanPanel).reset()
        self._buffer_streaming = ""
        self.query_one("#chat-streaming", Static).update("")

        self._ultima_orden = orden
        self._procesando = True
        self._en_debug_block = False
        self._refresh_top_bar()

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
        chat = self.query_one("#chat-log", RichLog)

        if isinstance(evento, bridge.NodeUpdateEvent):
            delta = evento.delta

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
                msg = delta.get("error_mensaje", "error")
                chat.write(Text(f"  ⎿ ✗ [{evento.nodo}] {msg}", style="yellow"))

        elif isinstance(evento, bridge.StdoutLineEvent):
            texto = evento.texto.strip()
            if not texto:
                return

            # Defensivo: a veces el motor emite varios mensajes pegados sin
            # salto de línea real entre ellos (típicamente conexiones MCP
            # consecutivas, ver captura del 2026-07-05: 4 mensajes "[MCP]:
            # conectado a server '...'" llegaron como un solo StdoutLineEvent
            # y el RichLog los wrappeó como un párrafo continuo). No tenemos
            # graph_nodes.py para arreglar el print() en origen, así que acá
            # separamos heurísticamente por los marcadores conocidos antes
            # de renderizar, para que cada mensaje caiga en su propia línea.
            partes = self._separar_mensajes_pegados(texto)
            for parte in partes:
                self._render_linea_stdout(chat, parte)

        elif isinstance(evento, bridge.TokenEvent):
            self._buffer_streaming += evento.fragmento
            streaming_widget = self.query_one("#chat-streaming", Static)
            streaming_widget.update(Text(self._buffer_streaming, style="dim"))

        elif isinstance(evento, bridge.DoneEvent):
            texto_final = self._buffer_streaming.strip() or evento.respuesta
            if texto_final.startswith("🎙️"):
                texto_final = texto_final.split(":", 1)[-1].strip()

            if texto_final:
                chat.write(Text(f"\n● {texto_final}", style="bold"))

            self._context_used = min(self._context_used + 1200, self._context_total)
            self._refresh_top_bar()
            self._refresh_bottom_bar()

            self.query_one("#chat-streaming", Static).update("")
            self._buffer_streaming = ""
            self._procesando = False

        elif isinstance(evento, bridge.ErrorEvent):
            chat.write(Text(f"\n✗ Error: {evento.mensaje}", style="bold red"))
            self.query_one("#chat-streaming", Static).update("")
            self._buffer_streaming = ""
            self._procesando = False

    def _separar_mensajes_pegados(self, texto: str) -> list[str]:
        """Divide un string que puede traer varios mensajes concatenados
        sin separador real (ver captura del 2026-07-05: mensajes de conexión
        MCP consecutivos llegando como un solo StdoutLineEvent), cortando
        antes de cada marcador reconocido."""
        patron = r"(?=\[MCP\]:|\[PLANNER\]:|\[PLAN EXECUTOR\]|🔧 \[|🚀|Intención decidida|\[MEMORIA\]|🎙️|🧠)"
        partes = [p.strip() for p in re.split(patron, texto) if p.strip()]
        # Descartar fragmentos que quedan como puro ícono/puntuación suelta
        # (p. ej. un "🧠" solo, residuo de cortar justo antes del texto real).
        return [p for p in partes if re.search(r"\w", p)]

    def _render_linea_stdout(self, chat: RichLog, texto: str) -> None:
        """Renderiza una línea de stdout ya individualizada, con el set
        único de marcadores tipográficos (sin emojis mezclados con color).

        El ruido interno del motor (conexiones MCP, dump de contexto,
        traza de "Intención decidida...", turnos de memoria) solo se
        muestra si el switch Debug está en ON (F2 → Debug). Si está OFF
        se oculta, sin perder nunca la respuesta real de Aether aunque
        venga pegada a una de estas trazas.
        """
        if not texto:
            return

        debug_on = bool(self._settings.get("debug", False))

        # ── Bloque "=== DEBUG: CONTEXT DUMP === ... ====================" ──
        # Puede venir como varias líneas separadas; trackeamos el estado
        # entre llamadas para ocultar el bloque completo cuando debug=off.
        if texto.startswith("=== DEBUG"):
            self._en_debug_block = True
            if debug_on:
                chat.write(Text(f"  ⎿ {texto}", style="dim"))
            return

        if self._en_debug_block:
            es_cierre = bool(re.fullmatch(r"=+", texto))
            if debug_on:
                chat.write(Text(f"  ⎿ {texto}", style="dim"))
            if es_cierre:
                self._en_debug_block = False
            return

        # ── Conexiones MCP: puro ruido de arranque, solo con debug=on ──
        if texto.startswith("[MCP]:") or "[MCP]:" in texto:
            if debug_on:
                chat.write(Text(f"  ⎿ {texto}", style="dim"))
            return

        # ── Traza "Intención decidida..." pegada a la respuesta real ──
        # El motor a veces pega esto directo antes de "Aether:..." sin
        # separador (ver captura 2026-07-05). La respuesta real SIEMPRE
        # se rescata y se muestra; la traza de debug solo si debug=on.
        if "Intención decidida por razonamiento del modelo" in texto:
            if debug_on:
                chat.write(Text(f"  ⎿ {texto.split('Aether:', 1)[0].strip()}", style="dim"))
            if "Aether:" in texto:
                contenido = texto.split("Aether:", 1)[-1].strip()
                if contenido:
                    chat.write(Text(f"\n● {contenido}", style="bold"))
            return

        # ── Turno de memoria guardado: solo con debug=on ──
        if texto.startswith("[MEMORIA]"):
            if debug_on:
                chat.write(Text(f"  ⎿ {texto}", style="dim"))
            return

        if "🎙️" in texto or texto.startswith("Aether:"):
            contenido = texto.split("🎙️", 1)[-1].lstrip(" \t:").strip()
            if contenido:
                chat.write(Text(f"\n● {contenido}", style="bold"))

        elif texto.startswith("🚀") or "[LAUNCH]" in texto:
            resumen = texto.split("]", 1)[-1].strip() if "]" in texto else texto
            chat.write(Text(f"  ⎿ {resumen}", style="dim"))

        elif "[PLAN EXECUTOR]" in texto or "🔧 [" in texto:
            clean = texto.replace("🔧 ", "").replace("[PLAN EXECUTOR]", "plan")
            chat.write(Text(f"  ⎿ {clean}", style="dim"))

        elif "Lanzado" in texto and "PID" in texto:
            chat.write(Text(f"  ⎿ {texto}", style="dim"))

        elif any(x in texto for x in ["🖥️", "💻", "👁️", "🌐"]):
            chat.write(Text(f"  ⎿ {texto}", style="dim"))

        elif texto.startswith("❌") or "error" in texto.lower():
            chat.write(Text(f"  ⎿ ✗ {texto.lstrip('❌ ')}", style="bold yellow"))

        else:
            chat.write(Text(f"  ⎿ {texto}", style="dim"))

    # ──────────────────────────────────────────────────────────────
    # ACCIONES
    # ──────────────────────────────────────────────────────────────

    def action_salir_app(self) -> None:
        self.exit()

    def action_open_settings(self) -> None:
        """F2 → modal real de configuración (ConfigScreen), no texto en el chat."""
        def _al_cerrar(_resultado=None) -> None:
            # Al volver del modal, refrescar el header por si cambió el modelo/temp/etc.
            self._apply_config_to_settings()
            self._refresh_top_bar()

        self.push_screen(ConfigScreen(), _al_cerrar)

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar")
        self._sidebar_visible = not self._sidebar_visible
        if self._sidebar_visible:
            sidebar.add_class("visible")
        else:
            sidebar.remove_class("visible")

    def action_clear_chat(self) -> None:
        self.query_one("#chat-log", RichLog).clear()
        self.query_one("#chat-log", RichLog).write(Text("chat limpiado.", style="dim"))

    def action_retry_last(self) -> None:
        if not self._ultima_orden:
            self.query_one("#chat-log", RichLog).write(Text("nada para reintentar todavía.", style="dim"))
            return
        if self._procesando:
            self.query_one("#chat-log", RichLog).write(Text("todavía estoy procesando la orden anterior...", style="dim"))
            return

        chat = self.query_one("#chat-log", RichLog)
        chat.write(Text(f"\n› {self._ultima_orden}  (retry)", style="bold"))

        self.query_one(PlanPanel).reset()
        self._buffer_streaming = ""
        self.query_one("#chat-streaming", Static).update("")

        self._procesando = True
        self._refresh_top_bar()

        orden = self._ultima_orden
        self.run_worker(
            lambda: self._procesar_orden(orden),
            thread=True,
            exclusive=False,
            name="orden",
        )


def run() -> None:
    AetherApp().run()
