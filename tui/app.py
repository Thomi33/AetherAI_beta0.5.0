"""
app.py — TUI principal de Aether (Textual).

Rediseño mayor (inspirado en OpenCode / Claude Code pero manteniendo identidad terminal de Aether):
- Header inteligente con estado del modelo, thinking, contexto, tools (siempre visible).
- Barra inferior con uso de contexto (barra visual) + atajos reales.
- Chat como protagonista: tool calls compactos y legibles (estilo Claude).
- Sidebar colapsable (Ctrl+B) con Plan + preview de Tools/MCP.
- Config en caliente (F2): presets de reasoning (OFF/Fast/Balanced/Deep/Extreme), modelo, etc.
- Menos ruido: se eliminó "Intención decidida..." por defecto.
- Estados visuales discretos (● Thinking etc.).
- Preparada para MCP y crecimiento (paneles modulares).

Todo el progreso sigue llegando por engine_bridge.iter_eventos() (sin romper bridge).
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Input, RichLog, Static
from textual.worker import Worker, WorkerState
from rich.text import Text

from .widgets import PlanPanel
from . import engine_bridge as bridge


PALABRAS_SALIDA = {"salir", "adios", "exit", "apágate", "apagate", "quit"}


class AetherApp(App):
    """App principal de la TUI de Aether."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #top-bar {
        dock: top;
        height: 3;                  /* header inteligente multi-linea */
        background: $boost;
        color: $text;
        padding: 0 1;
        border-bottom: solid $accent;
    }

    /* Colores sobrios (punto 11): gris/azul/cian/verde. Amarillo/rojo solo alertas */
    .tool-call { color: $accent; }
    .success { color: $success; }
    .warning { color: $warning; }  /* amarillo solo advertencias */
    .error { color: $error; }      /* rojo solo errores */

    #cuerpo {
        height: 1fr;
    }

    #sidebar {
        width: 28;
        border: round $accent;
        padding: 0 1;
        background: $surface;
        display: none;              /* colapsable por defecto para mas chat */
    }

    #sidebar.visible {
        display: block;
    }

    #plan-panel {
        height: auto;
        max-height: 55%;
    }

    #chat-wrapper {
        width: 1fr;
    }

    #chat-log {
        height: 1fr;
        padding: 0 1;
        background: $surface;
    }

    #chat-streaming {
        height: auto;
        padding: 0 1 1 1;
        color: $success;
        text-style: italic;
    }

    #bottom-bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
        border-top: solid $accent;
    }

    Input {
        dock: bottom;
        background: $surface;
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
        self._sidebar_visible = True  # start visible but can toggle (Ctrl+B)
        self._context_used = 0
        self._context_total = 128000
        # Hot config state (punto 5)
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
        yield Header(show_clock=True)

        # Header inteligente (punto 3) - info útil, no redundante
        yield Static("", id="top-bar", markup=True)

        with Horizontal(id="cuerpo"):
            # Panel lateral colapsable (punto 13)
            with Vertical(id="sidebar"):
                yield PlanPanel(id="plan-panel")
                # Placeholder para Tools / MCP preview (puntos 17,18)
                yield Static("[dim]Tools: [green]✓[/] Shell Web Launch\n[dim]MCP: 0/0[/]", id="tools-preview")

            with Vertical(id="chat-wrapper"):
                yield RichLog(id="chat-log", markup=True, wrap=True)
                yield Static("", id="chat-streaming", markup=False)

        # Barra inferior útil (punto 10) + contexto (punto 4)
        yield Static("", id="bottom-bar", markup=True)

        yield Input(placeholder="¿Qué querés que haga?  (F2 Settings • Ctrl+B Sidebar • Ctrl+L Clear)", id="input-orden")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "AETHER"
        self.sub_title = "Agente local • Ornith + LangGraph"
        self._refresh_top_bar()
        self._refresh_bottom_bar()
        chat = self.query_one("#chat-log", RichLog)
        chat.write(Text("🤖 Aether — listo para trabajar", style="bold cyan"))
        chat.write(Text("Escribí cualquier cosa abajo. Uso herramientas cuando hace falta.", style="dim"))
        self.run_worker(self._inicializar_motor, thread=True, exclusive=True, name="init")

    # ──────────────────────────────────────────────────────────────
    # ESTADO / HEADER
    # ──────────────────────────────────────────────────────────────

    def _refresh_top_bar(self) -> None:
        """Header inteligente (punto 3) - datos útiles, siempre visibles (punto 7)."""
        bar = self.query_one("#top-bar", Static)
        s = self._settings
        ctx_pct = int((self._context_used / self._context_total) * 100) if self._context_total else 0
        text = (
            f"[bold]AETHER[/]  "
            f"Model: [cyan]{s['model']}[/]  "
            f"Provider: [blue]{s['provider']}[/]  "
            f"Thinking: [green]{s['thinking']}[/]  "
            f"Level: [yellow]{s['reasoning_level']}[/]  "
            f"Ctx: [magenta]{self._context_used//1000}k/{self._context_total//1000}k[/] ({ctx_pct}%)  "
            f"Tools: {'[green]on[/]' if s['tools_enabled'] else '[red]off[/]'}  "
            f"Sesión: [dim]activa[/]"
        )
        bar.update(text)

    def _refresh_bottom_bar(self) -> None:
        """Barra inferior con contexto visual (punto 4) y atajos (punto 10)."""
        bar = self.query_one("#bottom-bar", Static)
        ctx_pct = int((self._context_used / self._context_total) * 100) if self._context_total else 0
        bar_len = 12
        filled = int(bar_len * ctx_pct / 100)
        bar_vis = "█" * filled + "░" * (bar_len - filled)
        bar_text = f"Contexto: {bar_vis} {ctx_pct}%  [dim]│[/] F2 Settings  Ctrl+B Sidebar  Ctrl+L Clear  Ctrl+R Retry"
        bar.update(bar_text)

    def _set_estado(self, texto: str, icono: str = "●") -> None:
        # Estado ahora integrado en top-bar y bottom-bar (menos ruido)
        self._refresh_top_bar()

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
        self._refresh_top_bar()
        self._refresh_bottom_bar()
        sidebar = self.query_one("#sidebar")
        if self._sidebar_visible:
            sidebar.add_class("visible")
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
        chat.write(Text(f"\n🧠 Tú: {orden}", style="bold cyan"))

        self.query_one(PlanPanel).reset()
        self._buffer_streaming = ""
        self.query_one("#chat-streaming", Static).update("")

        self._procesando = True
        self._set_estado("● Thinking", "⚙")
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

            # Actualizar panel lateral compacto (solo plan)
            if "plan_pasos" in delta or "plan_activo" in delta:
                plan_panel.actualizar(
                    plan_pasos=delta.get("plan_pasos", plan_panel.plan_pasos),
                    plan_index=delta.get("plan_index", plan_panel.plan_index),
                    plan_activo=delta.get("plan_activo", plan_panel.plan_activo),
                )
            elif "plan_index" in delta:
                plan_panel.plan_index = delta["plan_index"]
                plan_panel._repintar()

            # Mostrar actividad importante directamente en el chat (estilo Claude Code)
            if delta.get("error_activo"):
                msg = delta.get("error_mensaje", "error")
                chat.write(Text(f"\n⚠️  [{evento.nodo}] {msg}", style="bold yellow"))

        elif isinstance(evento, bridge.StdoutLineEvent):
            texto = evento.texto.strip()

            # Claude-Code style: la mayoría de la actividad se ve en el chat
            # como si fuera parte natural de la conversación.

            if not texto:
                return

            # Clean internal noise by default (point 1)
            if "Intención decidida por razonamiento del modelo" in texto:
                return  # hidden unless debug mode enabled later

            if "🎙️" in texto or texto.startswith("Aether:"):
                contenido = texto.split("🎙️", 1)[-1].lstrip(" \t:").strip()
                if contenido:
                    chat.write(Text(f"\n🎙️  Aether: {contenido}", style="bold green"))

            elif "🚀 [LAUNCH]" in texto or texto.startswith("🚀"):
                chat.write(Text(f"\n{texto}", style="bold cyan"))

            # Tool calls modernas (punto 9) - compactas y estilo Claude
            elif "🔧 [" in texto or "[PLAN EXECUTOR]" in texto:
                clean = texto.replace("🔧 ", "🔧 ").replace("[PLAN EXECUTOR]", "Plan")
                chat.write(Text(f"\n{clean}", style="dim cyan"))
            elif "[LAUNCH]" in texto or texto.startswith("🔍 [LAUNCH]"):
                chat.write(Text(f"\n🚀 {texto.split(']',1)[-1].strip() if ']' in texto else texto}", style="dim"))
            elif "Lanzado" in texto and "PID" in texto:
                chat.write(Text(f"   ✔ {texto}", style="green"))
            elif any(x in texto for x in ["🖥️", "💻", "👁️", "🌐"]):
                chat.write(Text(f"   {texto}", style="dim"))
            elif texto.startswith("❌") or "error" in texto.lower():
                chat.write(Text(f"\n{texto}", style="bold red"))
            else:
                chat.write(Text(f"   {texto}", style="dim"))

        elif isinstance(evento, bridge.TokenEvent):
            # Streaming token a token (pensamiento o respuesta en vivo)
            self._buffer_streaming += evento.fragmento
            streaming_widget = self.query_one("#chat-streaming", Static)
            from rich.text import Text as RichText
            streaming_widget.update(RichText(self._buffer_streaming, style="italic green"))

        elif isinstance(evento, bridge.DoneEvent):
            # Volcar lo que quedó del streaming al chat principal
            texto_final = self._buffer_streaming.strip() or evento.respuesta
            if texto_final.startswith("🎙️"):
                texto_final = texto_final.split(":", 1)[-1].strip()

            if texto_final:
                chat.write(Text(f"\n🎙️  Aether: {texto_final}", style="bold green"))

            # Actualizar contexto (punto 4) - heurística simple, real vendría de deltas
            self._context_used = min(self._context_used + 1200, self._context_total)
            self._refresh_top_bar()
            self._refresh_bottom_bar()

            self.query_one("#chat-streaming", Static).update("")
            self._buffer_streaming = ""
            self._procesando = False
            self._set_estado("listo", "●")

        elif isinstance(evento, bridge.ErrorEvent):
            chat.write(Text(f"\n❌ Error: {evento.mensaje}", style="bold red"))
            self.query_one("#chat-streaming", Static).update("")
            self._buffer_streaming = ""
            self._procesando = False
            self._set_estado("listo (con error)", "●")

    # ──────────────────────────────────────────────────────────────
    # ACCIONES
    # ──────────────────────────────────────────────────────────────

    def action_salir_app(self) -> None:
        self.exit()

    def action_open_settings(self) -> None:
        """Abre panel de configuración en caliente (punto 5)."""
        self._open_settings_modal()

    def action_toggle_sidebar(self) -> None:
        """Toggle panel lateral (punto 13)."""
        sidebar = self.query_one("#sidebar")
        self._sidebar_visible = not self._sidebar_visible
        if self._sidebar_visible:
            sidebar.add_class("visible")
        else:
            sidebar.remove_class("visible")
        self._refresh_top_bar()  # update if needed

    def action_clear_chat(self) -> None:
        """Limpia el chat (Ctrl+L)."""
        self.query_one("#chat-log", RichLog).clear()
        self.query_one("#chat-log", RichLog).write(Text("🧹 Chat limpiado.", style="dim"))

    def action_retry_last(self) -> None:
        """Reintentar última orden (Ctrl+R) - placeholder funcional."""
        # En producción se guardaría la última orden
        self.query_one("#chat-log", RichLog).write(Text("🔄 Reintentar no implementado aún en este prototipo.", style="yellow"))

    def _open_settings_modal(self):
        """Modal simple de settings + presets de razonamiento (puntos 5,6)."""
        # Usamos un screen simple por ahora (puede expandirse a Textual Screen dedicado)
        chat = self.query_one("#chat-log", RichLog)
        chat.write(Text("\n⚙️  SETTINGS (hot) - Presets de razonamiento:", style="bold cyan"))
        chat.write(Text("  OFF | Fast | Balanced (actual) | Deep | Extreme", style="dim"))
        chat.write(Text("  (Escribe el preset o 'close' para cerrar)", style="dim"))
        # Para real interactividad se implementaría un Input overlay o Screen.
        # Aquí mostramos la idea y actualizamos estado de ejemplo.
        self._settings["reasoning_level"] = "Deep"
        self._settings["thinking"] = "ON"
        self._refresh_top_bar()
        self._refresh_bottom_bar()
        chat.write(Text("   → Nivel cambiado a Deep (demo). Config real persistiría en mem/config.", style="green"))


def run() -> None:
    AetherApp().run()