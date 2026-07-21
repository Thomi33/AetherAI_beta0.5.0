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

from tui.widgets.slash_completer import SlashCompleter
from tui.widgets.selector_screens import (
    ModelSelectorScreen,
    AgentSelectorScreen,
    ThemeSelectorScreen,
    McpSelectorScreen,
    EffortSelectorScreen,
    CommandPaletteScreen,
)

from tui.engine_bridge import (
    iter_eventos,
    inicializar_motor,
    obtener_historial_para_mostrar,
    NodeUpdateEvent,
    StdoutLineEvent,
    TokenEvent,
    DoneEvent,
    ErrorEvent,
)

SLASH_HELP = (
    "Comandos disponibles:\n"
    "  /help                  — esta ayuda\n"
    "  /debug                 — muestra/oculta el panel de debug\n"
    "  /get [clave]           — ver config actual (todas, o una clave puntual)\n"
    "  /set <clave> <valor>   — cambiar un valor de config en caliente\n"
    "  /sesiones              — listar sesiones anteriores\n"
    "  /historial <n>         — ver la sesión #n de esa lista\n"
    "  /historial actual      — volver a la sesión en vivo\n"
    "  /models                — cambiar modelo de Ollama en vivo\n"
    "  /themes                — cambiar tema visual\n"
    "  /mcps                  — toggle servidores MCP\n"
    "  /effort                — nivel de esfuerzo (low/medium/high/max)\n"
    "  /agents                — cambiar agente\n"
    "  /new                   — nueva sesión"
)


class AetherApp(App):
    """Aplicación TUI principal de Aether."""

    BINDINGS = [
        ("tab", "open_agents", "agents"),
        ("ctrl+p", "open_commands", "commands"),
    ]

    CSS = """
    Screen { background: $surface; color: $text; }
    #chat_panel { height: 1fr; }
    #streaming_line { height: auto; color: $secondary-lighten-2; padding: 0 1; }
    #plan_panel, #status_bar, #debug_panel { dock: bottom; }
    #status_bar { height: 2; background: $primary; color: $text; padding: 0 1; }
    #debug_panel { height: 10; background: $surface; color: $text; padding: 0 1; border-top: solid $accent; }
    #plan_panel { height: 6; background: $surface-darken-1; color: $text; padding: 0 1; }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._thinking = False
        self._respuesta_en_curso = ""
        self._ultimas_sesiones: list[dict] = []  # cache para '/historial <n>' tras '/sesiones'
        self._current_agent = "build"
        self._current_effort = "medium"

    def compose(self) -> ComposeResult:
        yield Header()
        yield ChatPanel(id="chat_panel")
        yield Static("", id="streaming_line")
        yield PlanPanel(id="plan_panel")
        yield DebugPanel(id="debug_panel")
        yield StatusBar(id="status_bar")
        yield SlashCompleter(id="slash_completer")
        yield Input(placeholder="Escribí tu mensaje aquí...", id="input_chat")
        yield Button("Enviar", id="btn_enviar")
        yield Footer()

    def on_mount(self) -> None:
        self._inicializar_motor_bg()

    @work(thread=True)
    def _inicializar_motor_bg(self) -> None:
        try:
            inicializar_motor()
            historial = obtener_historial_para_mostrar()
            chat_panel = self.query_one("#chat_panel", ChatPanel)
            self.call_from_thread(chat_panel.cargar_historial, historial)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(
                self.query_one("#chat_panel", ChatPanel).agregar_mensaje,
                f"⚠️ Error inicializando el motor: {e}",
                "assistant",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_enviar":
            self._enviar_mensaje()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "input_chat":
            sc = self.query_one("#slash_completer", SlashCompleter)
            sc.update_query(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input_chat":
            event.stop()
            sc = self.query_one("#slash_completer", SlashCompleter)
            # Si el completer está visible, Tab/Enter confirma el comando
            if sc.display:
                cmd = sc.confirm()
                if cmd:
                    input_widget = self.query_one("#input_chat", Input)
                    input_widget.value = cmd + " "
                    input_widget.cursor_position = len(input_widget.value)
                return
            self._enviar_mensaje()

    def on_key(self, event) -> None:
        """Flechas arriba/abajo navegan el completer; Escape lo cierra."""
        sc = self.query_one("#slash_completer", SlashCompleter)
        if not sc.display:
            return
        if event.key == "up":
            sc.move_up()
            event.stop()
        elif event.key == "down":
            sc.move_down()
            event.stop()
        elif event.key == "escape":
            sc.hide()
            event.stop()
        elif event.key == "tab":
            cmd = sc.confirm()
            if cmd:
                input_widget = self.query_one("#input_chat", Input)
                input_widget.value = cmd + " "
                input_widget.cursor_position = len(input_widget.value)
            event.stop()

    def _enviar_mensaje(self) -> None:
        if self._thinking:
            return

        input_widget = self.query_one("#input_chat", Input)
        texto = input_widget.value.strip()
        if not texto:
            return
        # Si el completer está visible al enviar, cerrarlo
        self.query_one("#slash_completer", SlashCompleter).hide()
        input_widget.value = ""

        if texto.startswith("/"):
            self._manejar_comando_slash(texto)
            return

        chat_panel = self.query_one("#chat_panel", ChatPanel)
        chat_panel.agregar_mensaje(texto, "user")

        self._thinking = True
        self._respuesta_en_curso = ""
        input_widget.disabled = True
        self._procesar_orden_bg(texto)

    def _manejar_comando_slash(self, texto: str) -> None:
        """Comandos '/' que se resuelven localmente en la TUI, sin pasar por el grafo."""
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        partes = texto.strip().split(maxsplit=2)
        cmd = partes[0].lower()

        if cmd in ("/help", "/?"):
            chat_panel.agregar_mensaje(SLASH_HELP, "assistant")

        elif cmd == "/debug":
            debug_panel = self.query_one("#debug_panel", DebugPanel)
            debug_panel.display = not debug_panel.display
            estado = "visible" if debug_panel.display else "oculto"
            chat_panel.agregar_mensaje(f"Panel de debug: {estado}", "assistant")

        elif cmd == "/get":
            from core.config.config_manager import get_config_manager
            config = get_config_manager()
            clave = partes[1].upper() if len(partes) > 1 else None
            if clave:
                valor = config.get(clave)
                if valor is None and clave not in config.get_all():
                    texto_resp = f"⚠️ Clave desconocida: {clave}"
                else:
                    texto_resp = f"{clave} = {valor}"
            else:
                todas = config.get_all()
                texto_resp = "\n".join(f"{k} = {v}" for k, v in sorted(todas.items()))
            chat_panel.agregar_mensaje(texto_resp, "assistant")

        elif cmd == "/set":
            from core.config.config_manager import get_config_manager
            if len(partes) < 3:
                chat_panel.agregar_mensaje("Uso: /set <clave> <valor>  (ej: /set temperature 0.8)", "assistant")
            else:
                config = get_config_manager()
                clave = partes[1].upper()
                valor_raw = partes[2]
                # Intentar castear a tipo razonable (bool/int/float) antes de validar
                if valor_raw.lower() in ("true", "false"):
                    valor = valor_raw.lower() == "true"
                else:
                    try:
                        valor = int(valor_raw)
                    except ValueError:
                        try:
                            valor = float(valor_raw)
                        except ValueError:
                            valor = valor_raw

                # Claves que en realidad viven ANIDADAS dentro de
                # OLLAMA_GEN_OPTIONS (num_gpu, num_batch, num_thread, etc.).
                # Sin este mapeo, `/set num_gpu 32` crea una clave huérfana
                # de nivel superior que _llm_chat nunca lee (bug real que
                # nos costó una sesión entera de debugging).
                _CLAVES_GEN_OPTIONS = {"NUM_GPU", "NUM_BATCH", "NUM_THREAD", "TOP_P", "TOP_K"}
                if clave in _CLAVES_GEN_OPTIONS:
                    gen_opts = dict(config.get("OLLAMA_GEN_OPTIONS", {}) or {})
                    gen_opts[clave.lower()] = valor
                    ok = config.set("OLLAMA_GEN_OPTIONS", gen_opts, validate=True)
                    if ok:
                        texto_resp = f"✅ OLLAMA_GEN_OPTIONS.{clave.lower()} = {valor}"
                    else:
                        texto_resp = f"❌ No se pudo aplicar OLLAMA_GEN_OPTIONS.{clave.lower()} = {valor}"
                    chat_panel.agregar_mensaje(texto_resp, "assistant")
                else:
                    ok = config.set(clave, valor, validate=True)
                    if ok:
                        texto_resp = f"✅ {clave} = {valor}"
                    else:
                        texto_resp = f"❌ No se pudo aplicar {clave} = {valor} (validación falló o clave inválida)"
                    chat_panel.agregar_mensaje(texto_resp, "assistant")

        elif cmd == "/sesiones":
            self._listar_sesiones()

        elif cmd == "/historial":
            arg = partes[1] if len(partes) > 1 else ""
            self._cambiar_vista_historial(arg)

        elif cmd == "/models":
            self._abrir_model_selector()

        elif cmd == "/themes":
            self._abrir_theme_selector()

        elif cmd == "/mcps":
            self._abrir_mcp_selector()

        elif cmd == "/effort":
            self._abrir_effort_selector()

        elif cmd == "/agents":
            self._abrir_agent_selector()

        elif cmd == "/new":
            self._nueva_sesion()

        else:
            chat_panel.agregar_mensaje(f"⚠️ Comando desconocido: {cmd}. Probá /help", "assistant")

    def _listar_sesiones(self) -> None:
        from core.memory.memory_manager import listar_sesiones
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        sesiones = listar_sesiones()
        self._ultimas_sesiones = sesiones
        if not sesiones:
            chat_panel.agregar_mensaje("No hay sesiones guardadas todavía.", "assistant")
            return
        lineas = ["Sesiones recientes (usá /historial <n> para verlas):"]
        for i, s in enumerate(sesiones, start=1):
            preview = s["preview"] or "(sin mensajes de usuario)"
            lineas.append(f"  {i}. [{s['inicio']}] {s['turnos']} turnos — {preview}")
        chat_panel.agregar_mensaje("\n".join(lineas), "assistant")

    # ── Acciones de teclado (Tab / Ctrl+P) ──────────────────────────────────

    def action_open_agents(self) -> None:
        """Tab → abre el selector de agente."""
        if not self._thinking:
            self._abrir_agent_selector()

    def action_open_commands(self) -> None:
        """Ctrl+P → abre la paleta de comandos."""
        if not self._thinking:
            self._abrir_command_palette()

    # ── Selectores modales ────────────────────────────────────────────────────

    def _abrir_model_selector(self) -> None:
        from core.config.config_manager import get_config_manager
        config = get_config_manager()
        current = config.get("MODELO", "ornith:9b")

        def _on_model(model: str | None) -> None:
            if model:
                # Aceptar cualquier modelo sin validar (viene de ollama list)
                config.set("MODELO", model, validate=False)
                self._actualizar_status_bar()
                self.query_one("#chat_panel", ChatPanel).agregar_mensaje(
                    f"Modelo cambiado a [bold]{model}[/bold]", "assistant"
                )

        self.push_screen(ModelSelectorScreen(current_model=current), _on_model)

    def _abrir_theme_selector(self) -> None:
        from core.config.config_manager import get_config_manager
        config = get_config_manager()
        current = config.get("THEME", "system")

        def _on_theme(theme: str | None) -> None:
            if theme:
                # Intentar aplicar tema nativo de Textual. Muchos nombres de la lista
                # (estilo OpenCode: cobalt2, cursor, opencode, etc.) no existen en el
                # registro de temas de Textual y no tienen paleta implementada aca,
                # asi que solo se guarda la preferencia y se avisa que no hay render.
                aplicado = False
                try:
                    self.theme = theme
                    aplicado = True
                except Exception:
                    aplicado = False
                config.set("THEME", theme, validate=False)
                chat_panel = self.query_one("#chat_panel", ChatPanel)
                if aplicado:
                    chat_panel.agregar_mensaje(
                        f"Tema aplicado: [bold]{theme}[/bold]", "assistant"
                    )
                else:
                    chat_panel.agregar_mensaje(
                        f"Guardado '{theme}' como preferencia, pero Textual no tiene ese tema "
                        f"registrado nativamente, asi que no se ve reflejado visualmente todavia. "
                        f"Temas nativos disponibles: {', '.join(sorted(self.available_themes.keys()))}",
                        "assistant"
                    )

        self.push_screen(ThemeSelectorScreen(current_theme=current), _on_theme)

    def _abrir_mcp_selector(self) -> None:
        def _on_done(states: dict | None) -> None:
            if states is None:
                return
            # Persistir el estado de los toggles en mcp_servers.json
            import json, pathlib
            p = pathlib.Path(__file__).parent.parent / "core" / "config" / "mcp_servers.json"
            try:
                data = json.loads(p.read_text())
                if isinstance(data, list):
                    for s in data:
                        name = s.get("name", s.get("url", ""))
                        if name in states:
                            s["enabled"] = states[name]
                    out = data
                elif isinstance(data, dict) and "servers" in data:
                    for s in data["servers"]:
                        name = s.get("name", s.get("url", ""))
                        if name in states:
                            s["enabled"] = states[name]
                    out = data
                elif isinstance(data, dict):
                    # Formato real: {"nombre_server": {config...}, ...}
                    for name, cfg in data.items():
                        if name in states and isinstance(cfg, dict):
                            cfg["enabled"] = states[name]
                    out = data
                else:
                    out = data
                p.write_text(json.dumps(out, indent=2, ensure_ascii=False))
            except Exception:
                pass
            enabled = [k for k, v in states.items() if v]
            self.query_one("#chat_panel", ChatPanel).agregar_mensaje(
                f"MCPs activos: {', '.join(enabled) or 'ninguno'}", "assistant"
            )

        self.push_screen(McpSelectorScreen(on_done=_on_done), _on_done)

    def _abrir_effort_selector(self) -> None:
        def _on_effort(effort: str | None) -> None:
            if effort:
                self._current_effort = effort
                # Mapear effort → parámetros LLM.
                #
                # temperature: baja a medida que sube el effort (menos aleatoriedad,
                # respuestas más deterministas/precisas en 'max').
                #
                # num_predict: sube a medida que sube el effort. Este es el lever
                # real de "dejar que razone más" — es el presupuesto de tokens de
                # generación, que incluye el bloque <think> de Ornith. La temperatura
                # sola NO hace que el modelo piense más, solo cambia qué tan
                # aleatoria es cada eleccion de token; sin mas num_predict, 'max'
                # igual se cortaria al mismo largo que 'low'.
                #
                # num_ctx: sube en paralelo para que el razonamiento largo (mas
                # num_predict) tenga lugar en la ventana de contexto.
                effort_map = {
                    "low":    {"temperature": 0.9,  "num_ctx": 4096,  "num_predict": 768},
                    "medium": {"temperature": 0.6,  "num_ctx": 8192,  "num_predict": 2048},
                    "high":   {"temperature": 0.35, "num_ctx": 16384, "num_predict": 4096},
                    "max":    {"temperature": 0.15, "num_ctx": 32768, "num_predict": 8192},
                }
                from core.config.config_manager import get_config_manager
                config = get_config_manager()
                params = effort_map.get(effort, {})
                for k, v in params.items():
                    config.set(k.upper(), v, validate=False)
                self._actualizar_status_bar()
                self.query_one("#chat_panel", ChatPanel).agregar_mensaje(
                    f"Effort: [bold]{effort}[/bold] "
                    f"(temperatura {params.get('temperature')}, "
                    f"ctx {params.get('num_ctx')}, "
                    f"max tokens de razonamiento {params.get('num_predict')})",
                    "assistant"
                )

        self.push_screen(EffortSelectorScreen(current=self._current_effort), _on_effort)

    def _abrir_agent_selector(self) -> None:
        def _on_agent(agent: str | None) -> None:
            if agent:
                self._current_agent = agent
                self._actualizar_status_bar()
                self.query_one("#chat_panel", ChatPanel).agregar_mensaje(
                    f"Agente: [bold]{agent}[/bold]", "assistant"
                )

        self.push_screen(AgentSelectorScreen(current_agent=self._current_agent), _on_agent)

    def _abrir_command_palette(self) -> None:
        def _on_cmd(cmd: str | None) -> None:
            if cmd:
                # Simular que el usuario escribió el comando
                self._manejar_comando_slash(cmd)

        self.push_screen(CommandPaletteScreen(), _on_cmd)

    def _nueva_sesion(self) -> None:
        """Limpia la vista y reinicia la sesión."""
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        chat_panel.reset()
        self.query_one("#plan_panel", PlanPanel).update("")
        self.query_one("#debug_panel", DebugPanel).reset()
        chat_panel.agregar_mensaje("Nueva sesión iniciada.", "assistant")

    def _actualizar_status_bar(self) -> None:
        """Refresca la barra de estado con el modelo, agente y effort actuales."""
        from core.config.config_manager import get_config_manager
        config = get_config_manager()
        sb = self.query_one("#status_bar", StatusBar)
        sb.actualizar(
            model=config.get("MODELO", "ornith:9b"),
            provider="Ollama",
            agent=self._current_agent,
            effort=self._current_effort,
        )
        sb._repintar()

    def _cambiar_vista_historial(self, arg: str) -> None:
        from core.memory.memory_manager import obtener_turnos_por_sesion
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        arg = arg.strip().lower()

        if arg in ("", "actual", "vivo", "live"):
            chat_panel.volver_a_sesion_actual()
            return

        if not arg.isdigit() or not self._ultimas_sesiones:
            chat_panel.agregar_mensaje("Usá '/sesiones' primero, después '/historial <n>'.", "assistant")
            return

        idx = int(arg) - 1
        if idx < 0 or idx >= len(self._ultimas_sesiones):
            chat_panel.agregar_mensaje(f"No hay sesión #{arg}. Probá '/sesiones' de nuevo.", "assistant")
            return

        sesion = self._ultimas_sesiones[idx]
        turnos = obtener_turnos_por_sesion(sesion["sesion_id"])
        chat_panel.mostrar_sesion(turnos, etiqueta=f"{sesion['inicio']} ({sesion['turnos']} turnos)")

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