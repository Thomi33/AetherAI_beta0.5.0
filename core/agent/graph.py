"""
Grafo principal de Aether con LangGraph.

Reemplaza CrewAI (core/agent/builder.py + executor.py + Crew.kickoff())
por un StateGraph explícito. Mantiene TODA la lógica de negocio existente
(atajos sin LLM, análisis rápido, protocolo flatpak, etc.) — solo cambia
el mecanismo de orquestación.

Diferencias clave vs CrewAI:
- Sin parser ReAct basado en texto que se rompía con regex.
- Sin Crew.kickoff() de caja negra: cada nodo es una función pura.
- Streaming nativo en cada nodo vía .stream() de LangChain.

ERROR HANDLER (integrado al grafo, no un módulo aparte):
  Cubre los tres casos de fallo con diagnóstico web + confirmación ÚNICA
  + reintentos limitados por contexto:

  1. launch  — programa no encontrado o falla al lanzar      → hasta 5 intentos
  2. shell   — comando ejecutado que retorna error           → hasta 3 intentos
  3. codigo  — script generado que falla al ejecutar          → hasta 3 intentos

  (límites configurables en core/agent/graph_state.py → MAX_INTENTOS_POR_CONTEXTO)

  Flujo de error:
    fallo detectado
      → nodo_error_diagnose  (busca en web, LLM genera fix; valida límite de intentos)
      → nodo_error_confirm   (CONFIRMACIÓN ÚNICA: solo pregunta si error_autorizado
                               es False; si el usuario acepta, autoriza TODOS los
                               reintentos siguientes para este error sin volver a
                               preguntar, hasta el límite)
      → [usuario cancela en la primera confirmación → fin]
      → nodo_error_retry     (aplica el fix y ejecuta de nuevo, SIN pedir confirmación)
      → [sigue fallando y quedan intentos → nodo_error_diagnose]
      → [se alcanzó el límite → nodo_error_fallback → fin]
      → [si web no ayuda desde el inicio → nodo_error_fallback, estrategias locales]

USO DE TOOL CALLING:
  Configurable via TOOL_CALLING_NATIVO en settings.py.
  - True  → usa llm.bind_tools([...]) y deja que el modelo decida
  - False → flujo legado: buscar_web directo + LLM sintetiza
"""
from __future__ import annotations

import re
import difflib
from typing import Literal, Optional

from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START, END

from core.config.settings import MODELO, OLLAMA_HOST, TOOL_CALLING_NATIVO
from core.agent.graph_state import AetherState, MAX_INTENTOS_POR_CONTEXTO, MAX_INTENTOS_DEFAULT
from core.agent.prompts import construir_backstory
from core.memory.context_builder import construir_contexto_memoria
from core.memory.memory_manager import registrar_turno, registrar_comando, guardar_memoria
from core.tools.shell_executor import ejecutar_comando
from core.tools.flatpak_manager import buscar_flatpak_en_memoria, intentar_lanzar_flatpak
from core.tools.file_writer import escribir_archivo
from core.tools.vision import ver_pantalla
from core.tools.web_search import buscar_web
from core.tools.url_reader import leer_url
from core.parser.response_parser import analizar_salida

import json

# ══════════════════════════════════════════════════════════════════════
# DETECCIÓN DE INTENCIÓN + HELPERS (inline — sin depender de aether_service)
# ══════════════════════════════════════════════════════════════════════

_PALABRAS_CLAVE_ESCRITURA = frozenset([
    "escribe", "crea", "guardar", "guarda", "crear",
    "archivo", "txt", "reporte", "documento", "informe", "json", "md",
])

_PALABRAS_CLAVE_VISION = frozenset([
    "screenshot", "captura de pantalla", "captura la pantalla",
    "qué ves en", "que ves en", "qué contenido hay", "que contenido hay",
    "mira mi pantalla", "mira la pantalla", "observa mi pantalla",
    "observa la pantalla", "qué hay en pantalla", "que hay en pantalla",
    "en mi pantalla", "en la pantalla",
])

_PALABRAS_CLAVE_LANZAR = frozenset([
    "ejecuta", "abre", "lanza", "inicia", "corre"
])

_PALABRAS_CLAVE_WEB = frozenset([
    "busca", "buscar", "busca en internet", "busca en la web",
    "qué es", "que es", "cómo funciona", "como funciona",
    "cuál es", "cual es", "cuánto", "cuanto",
    "versión", "version", "última versión", "ultima version",
    "noticias", "precio", "investiga", "wikipedia",
    "actualmente", "hoy", "reciente", "último", "ultimo",
    "encuentra información", "qué dice", "que dice",
])

_CMDS_SILENCIOSOS = (
    "flatpak run", "xdg-open", "gtk-launch", "nohup",
    "systemctl start", "systemctl stop",
)

_STOP_WORDS_LANZAR = frozenset([
    "el", "la", "los", "las", "un", "una", "por", "favor", "me",
    "con", "sin", "en", "de", "del", "al",
])


def _necesita_web(orden: str) -> bool:
    o = orden.lower()
    return any(x in o for x in _PALABRAS_CLAVE_WEB)


def _contiene_vision(orden: str) -> bool:
    if _necesita_web(orden):
        return False
    return any(x in orden.lower() for x in _PALABRAS_CLAVE_VISION)


def _contiene_lanzar(orden: str) -> bool:
    return bool(set(orden.lower().split()) & _PALABRAS_CLAVE_LANZAR)


def _contiene_escritura(orden: str) -> bool:
    return bool(set(orden.lower().split()) & _PALABRAS_CLAVE_ESCRITURA)


def _buscar_flatpak_rapido(orden: str) -> str | None:
    stop_words = _PALABRAS_CLAVE_LANZAR | _STOP_WORDS_LANZAR
    palabras = [w for w in orden.lower().split() if w not in stop_words and len(w) > 2]
    if not palabras:
        return None
    for termino in reversed(palabras):
        # FIX #7: sanitizar contra shell injection en grep
        termino_safe = re.sub(r"[^\w\-\.]", "", termino)
        if not termino_safe:
            continue
        cmd = (
            f"flatpak list --app --columns=application,name 2>/dev/null "
            f"| grep -i '{termino_safe}' | head -1"
        )
        salida, hubo_error = ejecutar_comando(cmd)
        if salida and not hubo_error:
            partes = salida.strip().split()
            if partes and "." in partes[0]:
                return partes[0]
    return None


def _lanzar_programa_rapido(orden: str) -> str | None:
    stop_words = _PALABRAS_CLAVE_LANZAR | _STOP_WORDS_LANZAR
    palabras = [w for w in orden.lower().split() if w not in stop_words and len(w) > 2]
    if not palabras:
        return None
    for termino in reversed(palabras):
        # FIX #7: sanitizar contra shell injection — solo caracteres alfanuméricos,
        # guión y punto. Un término como "app'; rm -rf ~" queda como "app-rf".
        termino_safe = re.sub(r"[^\w\-\.]", "", termino)
        if not termino_safe:
            continue
        salida, hubo_error = ejecutar_comando(f"command -v '{termino_safe}' 2>/dev/null")
        if not hubo_error and salida.strip():
            return termino_safe
    return None


def _analisis_rapido(orden: str, comando: str, salida: str, hubo_error: bool) -> tuple[str | None, bool]:
    if any(cmd in comando for cmd in _CMDS_SILENCIOSOS):
        if hubo_error:
            detalle = salida.strip()[:200] if salida.strip() else "sin detalles"
            return f"Hubo un error al lanzar la aplicación: {detalle}", False
        return "Aplicación iniciada.", False
    if not salida.strip() and not hubo_error:
        return "Hecho. El comando se ejecutó sin errores.", False
    if hubo_error and len(salida) < 300:
        return f"Error: {salida.strip()}", False
    return None, True


def _procesar_comando_memoria(orden: str, mem: dict) -> bool:
    import re as _re
    o = orden.lower().strip()

    if any(x in o for x in ["muéstrame tu memoria", "qué recuerdas", "ver memoria", "mostrar memoria"]):
        print("\n📋 [MEMORIA DE Aether]:")
        print(json.dumps(mem, ensure_ascii=False, indent=2))
        return True

    if any(x in o for x in ["borra la conversación", "limpia la memoria conversacional", "olvida la conversación"]):
        mem["conversacion"] = []
        print("\n🎙️  Aether: Historial borrado.")
        return True

    if any(x in o for x in ["borra los flatpaks", "olvida los flatpaks", "actualiza flatpaks"]):
        mem["flatpaks"] = {}
        print("\n🎙️  Aether: Caché de Flatpaks reiniciado.")
        return True

    m = _re.search(r"(?:recuerda|anota|guarda)\s+(?:que\s+)?(.+)", orden, _re.IGNORECASE)
    if m and any(x in o for x in ["recuerda", "anota", "guarda que"]):
        nota = m.group(1).strip()
        mem["preferencias"]["notas"].append(nota)
        mem["preferencias"]["notas"] = mem["preferencias"]["notas"][-10:]
        guardar_memoria(mem)
        print(f"\n🎙️  Aether: Anotado: «{nota}»")
        return True

    guardado = False
    m = _re.search(r"mi nombre es ([A-Za-záéíóúÁÉÍÓÚñÑ]+)", orden, _re.IGNORECASE)
    if m:
        mem["preferencias"]["nombre_usuario"] = m.group(1).strip().capitalize()
        guardado = True
        print(f"\n🎙️  Aether: Nombre registrado: {mem['preferencias']['nombre_usuario']}.")

    m = _re.search(r"tengo (\d+) años", orden, _re.IGNORECASE)
    if m:
        mem["preferencias"]["edad"] = int(m.group(1))
        guardado = True
        print(f"\n🎙️  Aether: Edad registrada: {m.group(1)} años.")

    if guardado:
        guardar_memoria(mem)
        return True

    return False

# FIX #8: importación duplicada eliminada — ya importado en línea 53

# ══════════════════════════════════════════════════════════════════════
# EXTRACTOR DE COMANDOS SHELL (mejorado, sin crash con markdown)
# ══════════════════════════════════════════════════════════════════════

def extraer_comando_shell_seguro(texto: str) -> str | None:
    """
    Extrae comandos ejecutables del texto del LLM.
    Prioridad: [SHELL]...[/SHELL] > ```bash/sh/zsh > ReAct Action:
    Filtra falsos positivos de bloques descriptivos de Markdown.

    FIX #1: Los nombres de tools internas (buscar_web, leer_url, etc.) ya NO
    se tratan como comandos de shell — antes se ejecutaban en zsh y fallaban
    con "command not found", activando el error handler innecesariamente.
    """
    # Tools internas del agente — NO son binarios de sistema
    _TOOLS_INTERNAS = frozenset({
        "buscar_web", "search_web", "leer_url", "read_url",
        "ver_pantalla", "none",
    })

    if not texto:
        return None

    matches = []

    for m in re.finditer(r"\[SHELL\](.*?)\[/SHELL\]", texto, re.DOTALL):
        matches.append((m.start(), m.group(1).strip()))

    for m in re.finditer(r"```(?:bash|sh|zsh)\n(.*?)\n```", texto, re.DOTALL):
        matches.append((m.start(), m.group(1).strip()))

    for m in re.finditer(r"Action:\s*([\w-]+)\s*\nAction Input:\s*(.*)", texto, re.IGNORECASE):
        cmd  = m.group(1).strip()
        args = m.group(2).strip().split("\n")[0].strip()
        # FIX #1: ignorar tools internas — no son comandos zsh
        if cmd.lower() in _TOOLS_INTERNAS:
            continue
        if cmd.lower() != "none" and args.lower() != "none":
            matches.append((m.start(), f"{cmd} {args}"))

    if not matches:
        from core.parser.shell_parser import extraer_comando_shell
        res = extraer_comando_shell(texto)
        if res and not any(marker in res for marker in ("###", "**", "\n1. ", "Sintaxis", "Propuesta")):
            return res
        return None

    matches.sort(key=lambda x: x[0])
    return matches[0][1]


# ══════════════════════════════════════════════════════════════════════
# LLM SINGLETONS
# ══════════════════════════════════════════════════════════════════════

_llm_singleton: ChatOpenAI | None = None
_llm_ollama_singleton: ChatOllama | None = None
_llm_con_tools_singleton = None


def _get_llm() -> ChatOpenAI:
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = ChatOpenAI(
            model=MODELO,
            openai_api_key="ollama",
            openai_api_base=f"{OLLAMA_HOST}/v1",
            max_tokens=8192,
            temperature=0.1,
            streaming=True,
            extra_body={"think": False},
        )
    return _llm_singleton


def _get_llm_ollama() -> ChatOllama:
    global _llm_ollama_singleton
    if _llm_ollama_singleton is None:
        _llm_ollama_singleton = ChatOllama(
            model=MODELO,
            base_url=OLLAMA_HOST,
            temperature=0.1,
            num_ctx=16384,
        )
    return _llm_ollama_singleton


def _get_llm_con_tools():
    global _llm_con_tools_singleton
    if _llm_con_tools_singleton is None:
        _llm_con_tools_singleton = _get_llm_ollama().bind_tools([buscar_web, leer_url])
    return _llm_con_tools_singleton


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def _system_prompt(mem: dict) -> str:
    return construir_backstory(construir_contexto_memoria(mem))


def _confirmar_usuario(mensaje: str) -> bool:
    """Pide confirmación al usuario. Se usa SOLO una vez por error (primera vez)."""
    print(f"\n{mensaje}")
    resp = input("¿Autorizar? [S/n]: ").strip().lower()
    return resp in ("", "s", "si", "y", "yes")


def _max_intentos_para(contexto: str) -> int:
    """Retorna el límite de reintentos configurado para este tipo de error."""
    return MAX_INTENTOS_POR_CONTEXTO.get(contexto, MAX_INTENTOS_DEFAULT)


def _buscar_en_web_fix(error_msg: str, contexto: str = "shell") -> tuple[str, str]:
    """
    Busca el error en web y lee la primera URL relevante.
    Retorna (contexto_web_completo, url_fuente).
    """
    # FIX #6: usar solo la primera línea real del error para el query.
    # Antes se usaba el error_msg completo, que podía incluir texto del LLM
    # (ej: "accidente aéreo 2023") y contaminaba la búsqueda web.
    primera_linea = (error_msg.strip().splitlines() or [""])[0][:150]

    if contexto == "codigo":
        query = f"python error {primera_linea} solution fix"
    elif contexto == "launch":
        query = f"linux launch application not found {primera_linea[:120]} alternative"
    else:
        query = f"{primera_linea} linux zsh fix"

    print(f"\n🔍 [ERROR HANDLER]: Consultando web: '{query[:70]}...'")
    web_raw = buscar_web.invoke({"query": query})

    urls = re.findall(r"URL:\s*(https?://\S+)", web_raw)
    contenido_url = ""
    fuente = ""
    if urls:
        fuente = urls[0]
        print(f"📖 [ERROR HANDLER]: Leyendo {fuente}...")
        try:
            contenido_url = leer_url(fuente)[:2500]
        except Exception:
            contenido_url = ""

    contexto_web = web_raw
    if contenido_url:
        contexto_web += f"\n\n[CONTENIDO LEÍDO DE {fuente}]\n{contenido_url}"

    return contexto_web, fuente


def _hacer_diff(original: str, nuevo: str) -> str:
    """Genera diff unificado coloreado para mostrar en terminal."""
    orig_lines = original.splitlines(keepends=True)
    nuevo_lines = nuevo.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        orig_lines, nuevo_lines,
        fromfile="original", tofile="corregido", lineterm=""
    ))
    return "".join(diff) if diff else "(sin cambios detectados)"


def _imprimir_diff(diff_str: str) -> None:
    """Imprime diff con colores ANSI en terminal."""
    for linea in diff_str.splitlines():
        if linea.startswith("+") and not linea.startswith("+++"):
            print(f"\033[32m{linea}\033[0m")
        elif linea.startswith("-") and not linea.startswith("---"):
            print(f"\033[31m{linea}\033[0m")
        else:
            print(linea)


# ══════════════════════════════════════════════════════════════════════
# NODO: ROUTER
# ══════════════════════════════════════════════════════════════════════

def nodo_router(state: AetherState) -> AetherState:
    orden = state["orden"]
    mem   = state["mem"]

    registrar_turno(mem, "usuario", orden)

    if _procesar_comando_memoria(orden, mem):
        state["ruta"]      = "memoria"
        state["respuesta"] = None
        state["terminado"] = True
        return state

    if _contiene_vision(orden):
        state["ruta"] = "vision"
        return state

    if _contiene_lanzar(orden):
        state["ruta"] = "lanzar_app"
        return state

    if _necesita_web(orden):
        state["ruta"] = "web"
        return state

    state["ruta"] = "general"
    return state


def _despues_de_router(state: AetherState) -> Literal[
    "memoria", "vision", "lanzar_app", "web", "general"
]:
    return state["ruta"]


# ══════════════════════════════════════════════════════════════════════
# NODO: MEMORIA
# ══════════════════════════════════════════════════════════════════════

def nodo_memoria(state: AetherState) -> AetherState:
    state["terminado"] = True
    return state


# ══════════════════════════════════════════════════════════════════════
# NODO: VISIÓN
# ══════════════════════════════════════════════════════════════════════

def nodo_vision(state: AetherState) -> AetherState:
    import time
    orden = state["orden"]
    mem   = state["mem"]

    print("\n👁️  [Aether ACTIVANDO VISIÓN — Mueve el cursor al monitor deseado]")
    for _i in range(5, 0, -1):
        print(f"   ⏳ {_i}...", end="\r", flush=True)
        time.sleep(1)
    print("   📸 Capturando...                ")

    pregunta = orden if len(orden) > 10 else (
        "Analiza esta imagen técnicamente. Lista todos los elementos "
        "visibles: texto, ventanas, programas abiertos y su contenido."
    )
    descripcion = ver_pantalla(pregunta)
    print(f"\n🎙️  Aether: {descripcion}")

    registrar_turno(mem, "jarvis", descripcion)
    state["respuesta"] = descripcion
    state["terminado"] = True
    return state


# ══════════════════════════════════════════════════════════════════════
# NODO: LANZAR APP
# Ahora activa error handler si no encuentra el programa o falla al lanzar
# ══════════════════════════════════════════════════════════════════════

def _activar_error_handler(
    state: AetherState,
    contexto: str,
    mensaje: str,
    cmd: str = "",
    codigo_original: str = "",
    archivo: str = "",
) -> AetherState:
    """
    Fija todos los campos necesarios para entrar al loop de error handler
    de forma consistente. error_autorizado arranca en False: la PRIMERA vez
    que se llegue a error_confirm se pedirá confirmación; si el usuario
    acepta, queda autorizado para todos los reintentos siguientes de este
    mismo error, hasta error_max_intentos.
    """
    state["error_contexto"] = contexto
    state["error_mensaje"] = mensaje
    state["error_cmd"] = cmd
    state["error_codigo_original"] = codigo_original
    state["error_archivo"] = archivo
    state["error_intento"] = 0
    state["error_max_intentos"] = _max_intentos_para(contexto)
    state["error_autorizado"] = False
    state["terminado"] = False
    return state


def nodo_lanzar_app(state: AetherState) -> AetherState:
    orden = state["orden"]
    mem   = state["mem"]

    # 1. Caché de flatpaks en memoria
    app_id = buscar_flatpak_en_memoria(mem, orden)

    # 2. Grep en flatpak list
    if not app_id:
        print("\n🔍 [BÚSQUEDA RÁPIDA]: Buscando flatpak sin LLM...")
        app_id = _buscar_flatpak_rapido(orden)
        if app_id:
            nombre_clave = [w for w in orden.lower().split() if len(w) > 2]
            if nombre_clave:
                mem["flatpaks"][nombre_clave[-1]] = app_id
                guardar_memoria(mem)

    if app_id:
        cmd = f"flatpak run {app_id} &"
        print(f"\n🚀 [DIRECTO]: {app_id} encontrado. Lanzando sin LLM...")
        salida, hubo_error = ejecutar_comando(cmd)
        registrar_comando(mem, orden, cmd)

        if not hubo_error:
            respuesta = f"Lanzado {app_id}"
            print("   ✅ Lanzado.")
            registrar_turno(mem, "jarvis", respuesta)
            state["respuesta"] = respuesta
            state["terminado"] = True
            return state

        # Flatpak encontrado pero falló → error handler
        print(f"   ❌ Error al lanzar {app_id}: {salida[:150]}")
        return _activar_error_handler(state, "launch", salida, cmd=cmd)

    # 3. Ejecutable nativo en PATH
    print("\n🔍 [BÚSQUEDA RÁPIDA]: Buscando ejecutable en PATH...")
    cmd_path = _lanzar_programa_rapido(orden)
    if cmd_path:
        cmd = f"{cmd_path} &"
        print(f"\n🚀 [DIRECTO]: '{cmd_path}' encontrado en PATH. Lanzando sin LLM...")
        salida, hubo_error = ejecutar_comando(cmd)
        registrar_comando(mem, orden, cmd)

        if not hubo_error:
            respuesta = f"Lanzado {cmd_path}"
            print("   ✅ Lanzado.")
            registrar_turno(mem, "jarvis", respuesta)
            state["respuesta"] = respuesta
            state["terminado"] = True
            return state

        print(f"   ❌ Error al lanzar {cmd_path}: {salida[:150]}")
        return _activar_error_handler(state, "launch", salida, cmd=cmd)

    # 4. No encontrado → error handler para que busque alternativas
    print("\n❌ [LAUNCH]: Programa no encontrado. Activando diagnóstico...")
    return _activar_error_handler(
        state, "launch", f"No se encontró ningún ejecutable para: {orden}"
    )


def _despues_de_lanzar(state: AetherState) -> Literal["fin", "general", "error_diagnose"]:
    if state.get("terminado"):
        return "fin"
    if state.get("error_contexto"):
        return "error_diagnose"
    return "general"


# ══════════════════════════════════════════════════════════════════════
# NODO: WEB
# ══════════════════════════════════════════════════════════════════════

def _backstory_tool_calling(contexto_memoria: str) -> str:
    return f"""Eres Aether, un sistema de IA sofisticado y leal. Tu tono es preciso pero trata al Creador de forma amigable y respetuosa. Tienes acceso a herramientas de búsqueda web.

{contexto_memoria}

[USO DE HERRAMIENTAS]:
Tenés disponibles "Buscar en la Web con SearXNG" y "Leer Contenido de una URL".
Cuando necesites información actual o verificable, llamá a la herramienta
correspondiente usando tool calling — NO escribas "Action:" ni "Thought:"
como texto, el sistema ya te da las herramientas de forma estructurada.

[CRÍTICO — USAR LOS RESULTADOS REALES]:
Después de llamar a una herramienta, vas a recibir resultados con títulos,
URLs y resúmenes reales. DEBÉS basar tu respuesta final en esa información concreta.

Responde SIEMPRE en español, de forma natural y directa."""


def nodo_web(state: AetherState) -> AetherState:
    orden = state["orden"]
    mem   = state["mem"]

    print("\n🔍 [WEB]: Procesando consulta...")

    try:
        if TOOL_CALLING_NATIVO:
            respuesta = _web_con_tool_calling(orden, mem)
        else:
            respuesta = _web_directo_legacy(orden)
    except Exception as e:
        print(f"\n⚠️  [WEB]: Falló tool calling nativo ({e}), usando modo directo...")
        respuesta = _web_directo_legacy(orden)

    print(f"\n🎙️  Aether: {respuesta}")
    registrar_turno(mem, "jarvis", respuesta)
    state["respuesta"] = respuesta
    state["terminado"] = True
    return state


def _web_con_tool_calling(orden: str, mem: dict) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    contexto = construir_contexto_memoria(mem)
    llm      = _get_llm_con_tools()

    mensajes = [
        SystemMessage(content=_backstory_tool_calling(contexto)),
        HumanMessage(content=orden),
    ]

    TOOLS_MAP      = {"buscar_web": buscar_web, "leer_url": leer_url}
    queries_vistas: set[tuple] = set()

    respuesta_valida = None

    for ronda in range(3):
        ai_msg = llm.invoke(mensajes)
        mensajes.append(ai_msg)

        thinking = ai_msg.additional_kwargs.get("thinking") or getattr(ai_msg, "thinking", None)
        if thinking:
            print(f"\n🧠 [THINKING ronda {ronda}]: {thinking[:400]}...")

        if not ai_msg.tool_calls:
            # FIX #4: solo retornar si la respuesta tiene contenido sustancial.
            contenido = (ai_msg.content or "").strip()

            # FIX C: el modelo a veces alucina tool calls como texto plano
            # (XML <tool_call>, JSON con toolbench_rapidapi_key, etc.)
            # Si el contenido parece un tool call textual, ignorarlo y continuar.
            _es_tool_call_textual = (
                "toolbench_rapidapi_key" in contenido
                or "<tool_call>" in contenido
                or ('"arguments"' in contenido and '"name"' in contenido)
                or contenido.startswith('{"name":')
            )
            if _es_tool_call_textual:
                print(f"\n⚠️  [TOOL CALL TEXTUAL IGNORADO — ronda {ronda}]")
                respuesta_valida = None  # no usarla como fallback
                continue

            if len(contenido) > 30:
                return contenido
            respuesta_valida = contenido
            continue

        for tc in ai_msg.tool_calls:
            # FIX B: validar que los args requeridos estén presentes antes de invocar.
            # El modelo a veces alucina args inválidos (ej: toolbench_rapidapi_key)
            # que hacen explotar Pydantic. Los bloqueamos acá con un mensaje de sistema
            # para que el modelo corrija en la siguiente ronda.
            _ARGS_REQUERIDOS = {"buscar_web": "query", "leer_url": "url"}
            arg_req = _ARGS_REQUERIDOS.get(tc["name"])
            if arg_req and arg_req not in tc["args"]:
                print(f"\n⚠️  [TOOL CALL INVÁLIDO — falta '{arg_req}']: {tc['name']}({tc['args']})")
                mensajes.append(ToolMessage(
                    content=f"[SISTEMA]: llamada inválida a {tc['name']} — falta el argumento '{arg_req}'. Reintenta con el argumento correcto.",
                    tool_call_id=tc["id"],
                ))
                continue

            firma   = (tc["name"], tuple(sorted(tc["args"].items())))
            tool_fn = TOOLS_MAP.get(tc["name"])

            if firma in queries_vistas:
                resultado = (
                    "[SISTEMA]: Ya ejecutaste esta búsqueda exacta antes y "
                    "no dio resultados nuevos."
                )
                print(f"\n⚠️  [TOOL CALL DUPLICADO BLOQUEADO]: {tc['name']}({tc['args']})")
            else:
                queries_vistas.add(firma)
                print(f"\n🔧 [TOOL CALL]: {tc['name']}({tc['args']})")
                resultado = tool_fn.invoke(tc["args"]) if tool_fn else f"Herramienta desconocida: {tc['name']}"

            mensajes.append(ToolMessage(content=str(resultado), tool_call_id=tc["id"]))

    # FIX #4: si el loop terminó sin retornar (3 rondas con tool_calls o
    # respuestas muy cortas), forzar síntesis final. Si hay respuesta_valida
    # (corta pero existente) usarla solo si el LLM falla.
    try:
        final = _get_llm_ollama().invoke(mensajes)
        contenido_final = (final.content or "").strip()
        return contenido_final if contenido_final else (respuesta_valida or "No se encontró información relevante.")
    except Exception:
        return respuesta_valida or "No se encontró información relevante."


def _web_directo_legacy(orden: str) -> str:
    # FIX A: buscar_web es StructuredTool — no es callable directo, requiere .invoke()
    resultados = buscar_web.invoke({"query": orden})
    llm        = _get_llm()
    prompt = (
        f"El usuario pregunta: {orden}\n\n"
        f"Resultados de búsqueda web:\n{resultados}\n\n"
        "Responde en español de forma clara, precisa y concisa basándote "
        "exclusivamente en los resultados de búsqueda proporcionados."
    )
    return llm.invoke(prompt).content


# ══════════════════════════════════════════════════════════════════════
# NODO: GENERAL (texto + shell + escritura)
# Shell con error activa el error handler
# ══════════════════════════════════════════════════════════════════════

def nodo_general(state: AetherState) -> AetherState:
    orden   = state["orden"]
    mem     = state["mem"]
    contexto = construir_contexto_memoria(mem)
    llm     = _get_llm()

    print("\n🤔 [AETHER]: Pensando...", flush=True)

    tokens: list[str] = []
    thinking_mostrado  = False

    def _on_token(t: str) -> None:
        if not tokens:
            print("\n🎙️  Aether: ", end="", flush=True)
        tokens.append(t)
        print(t, end="", flush=True)

    respuesta_chunks = []
    for chunk in llm.stream([
        {"role": "system", "content": construir_backstory(contexto)},
        {"role": "user",   "content": orden},
    ]):
        thinking_chunk = (
            chunk.additional_kwargs.get("reasoning_content")
            or chunk.additional_kwargs.get("thinking")
        )
        if thinking_chunk:
            if not thinking_mostrado:
                print("\n🧠 [pensando]: ", end="", flush=True)
                thinking_mostrado = True
            print(thinking_chunk, end="", flush=True)

        token = chunk.content
        if token:
            _on_token(token)
            respuesta_chunks.append(token)

    if thinking_mostrado and not tokens:
        print()
    if tokens:
        print()

    respuesta = "".join(respuesta_chunks)
    if not respuesta.strip():
        respuesta = "Me quedé pensando demasiado y no llegué a responder..."
        print(f"\n🎙️  Aether: {respuesta}")

    state["respuesta"] = respuesta
    state["tokens"]    = tokens

    comando = extraer_comando_shell_seguro(respuesta)
    if comando:
        state["comando_shell"] = comando

    return state


def _despues_de_general(state: AetherState) -> Literal["shell", "escribir", "fin"]:
    palabras_escritura = (
        "escribe", "crea", "guardar", "guarda", "crear", "cree",
        "corrige", "corregir", "corrigelo", "script", ".py"
    )
    orden_lower = state["orden"].lower()
    quiere_escribir = any(p in orden_lower for p in palabras_escritura)

    # FIX #2: si el LLM generó un [SHELL] explícito en la respuesta, tiene
    # prioridad sobre la detección de escritura por keywords en la orden.
    # Antes, una orden como "crea un script y ejecútalo" iba a "escribir"
    # aunque el LLM hubiera generado [SHELL]...[/SHELL] — ese comando se perdía.
    hay_shell_explicito = bool(state.get("comando_shell"))
    if hay_shell_explicito:
        return "shell"
    if quiere_escribir or _contiene_escritura(state["orden"]):
        return "escribir"
    return "fin"


# ══════════════════════════════════════════════════════════════════════
# NODO: SHELL
# Si hubo_error → activa error handler en lugar de solo reportar
# ══════════════════════════════════════════════════════════════════════

def nodo_shell(state: AetherState) -> AetherState:
    orden        = state["orden"]
    mem          = state["mem"]
    comando      = state["comando_shell"]
    respuesta    = state["respuesta"]
    modo_autonomo = state.get("modo_autonomo", True)

    respuesta_limpia = re.sub(
        r"\[SHELL\].*?\[/SHELL\]|```[\w]*\n.*?\n```",
        "", respuesta, flags=re.DOTALL
    ).strip()

    if respuesta_limpia and not state.get("tokens"):
        print(f"\n🎙️  Aether: {respuesta_limpia}")

    print("\n⚠️  [SHELL DETECTADO]:")
    print(f"   \033[1;33m{comando}\033[0m")

    ejecutar = modo_autonomo
    if not ejecutar:
        conf = input("¿Autorizar ejecución? [S/n]: ").strip().lower()
        ejecutar = conf in ("", "s", "si", "y", "yes")

    if not ejecutar:
        print("\n❌ [SISTEMA]: Ejecución denegada.")
        state["respuesta"] = "Ejecución cancelada."
        state["terminado"] = True
        return state

    print("\n⚙️  [EJECUTANDO EN ZSH...]")
    salida, hubo_error = ejecutar_comando(comando)
    print(f"\n{'─'*50}\n{salida}\n{'─'*50}")
    registrar_comando(mem, orden, comando)

    if "flatpak list" in comando and not hubo_error:
        intentar_lanzar_flatpak(mem, salida, orden)

    state["salida_shell"] = salida
    state["hubo_error"]   = hubo_error

    if hubo_error:
        print(f"\n❌ [ERROR SHELL]: Activando diagnóstico automático...")
        return _activar_error_handler(state, "shell", salida, cmd=comando)

    return state


def _despues_de_shell(state: AetherState) -> Literal["analizar", "error_diagnose", "fin"]:
    if state.get("terminado"):
        return "fin"
    if state.get("hubo_error") and state.get("error_contexto"):
        return "error_diagnose"
    return "analizar"


def nodo_analizar_shell(state: AetherState) -> AetherState:
    orden      = state["orden"]
    mem        = state["mem"]
    comando    = state["comando_shell"]
    salida     = state["salida_shell"]
    hubo_error = state["hubo_error"]

    analisis, necesita_llm = _analisis_rapido(orden, comando, salida, hubo_error)
    if necesita_llm:
        print("\n🤖 [ANALIZANDO RESULTADO...]")
        analisis = analizar_salida(orden, comando, salida)

    print(f"\n🎙️  Aether: {analisis}")
    registrar_turno(mem, "jarvis", analisis)
    state["respuesta"]  = analisis
    state["ya_impreso"] = True   # FIX #3: nodo_fin no duplicará print ni registro
    state["terminado"]  = True
    return state


# ══════════════════════════════════════════════════════════════════════
# NODO: ESCRIBIR ARCHIVO
# Ejecución de prueba activa error handler si falla
# ══════════════════════════════════════════════════════════════════════

def nodo_escribir_archivo(state: AetherState) -> AetherState:
    orden     = state["orden"]
    mem       = state["mem"]
    respuesta = state["respuesta"]

    # ── Extraer solo el bloque de código si existe ──────────────────
    bloques = re.findall(r"```(?:python|bash|sh)?\s*\n(.*?)\n```", respuesta, re.DOTALL)
    contenido_limpio = bloques[0].strip() if bloques else respuesta.strip()

    # ── Detectar ruta original mencionada en la orden ────────────────
    # FIX #5: extensiones ampliadas — antes .yaml, .toml, .env, .js, .ts, etc.
    # no eran capturadas y el archivo se guardaba con nombre incorrecto.
    m_ruta = re.search(
        r"(/[\w/\-\.]+\.(?:py|sh|txt|md|json|yaml|yml|toml|env|js|ts|csv|ini|cfg|rs|go|c|cpp|h))",
        orden
    )
    ruta_original = m_ruta.group(1) if m_ruta else None

    if ruta_original:
        try:
            with open(ruta_original, "w", encoding="utf-8") as f:
                f.write(contenido_limpio)
            nombre_arch = ruta_original
            ok = True
        except Exception as e:
            nombre_arch = ruta_original
            ok = False
    else:
        nombre_arch, ok = escribir_archivo(orden, contenido_limpio)

    if not ok:
        print(f"\n❌ [SISTEMA]: Error de escritura en '{nombre_arch}'.")
        state["respuesta"] = f"No pude escribir el archivo '{nombre_arch}'."
        registrar_turno(mem, "jarvis", state["respuesta"])
        state["terminado"] = True
        return state

    print(f"\n⚙️  [SISTEMA]: Archivo '{nombre_arch}' guardado con éxito.")
    print(f"\n🎙️  Aether: Informe plasmado en '{nombre_arch}'.")

    _quiere_probar = any(
        p in orden.lower()
        for p in ("prueba", "probar", "ejecuta", "corre", "testea")
    )

    if not (_quiere_probar and nombre_arch.endswith((".py", ".sh"))):
        registrar_turno(mem, "jarvis", contenido_limpio)
        state["respuesta"] = contenido_limpio
        state["terminado"] = True
        return state

    cmd_prueba = (
        f"python3 {nombre_arch}" if nombre_arch.endswith(".py")
        else f"bash {nombre_arch}"
    )
    print(f"\n🧪 [SISTEMA]: Probando '{nombre_arch}'...")
    salida_prueba, hubo_error = ejecutar_comando(cmd_prueba)

    if hubo_error:
        print(f"\n⚠️  [ERROR AL PROBAR]:\n{salida_prueba[:300]}")
        try:
            with open(nombre_arch, "r", encoding="utf-8") as f:
                codigo_original = f.read()
        except Exception:
            codigo_original = contenido_limpio

        state = _activar_error_handler(
            state, "codigo", salida_prueba,
            cmd=cmd_prueba, codigo_original=codigo_original, archivo=nombre_arch,
        )
        state["salida_shell"] = salida_prueba
        state["hubo_error"]   = True
        return state

    print(f"   ✅ Ejecución exitosa:\n{salida_prueba[:300]}")
    respuesta_final = f"Guardado y ejecutado correctamente.\nResultado:\n{salida_prueba}"
    registrar_turno(mem, "jarvis", respuesta_final)
    state["respuesta"] = respuesta_final
    state["terminado"] = True
    return state


def _despues_de_escribir(state: AetherState) -> Literal["fin", "error_diagnose"]:
    if state.get("error_contexto"):
        return "error_diagnose"
    return "fin"

# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLER — NODO 1: DIAGNOSE
# Busca en web + LLM genera fix
# ══════════════════════════════════════════════════════════════════════

def nodo_error_diagnose(state: AetherState) -> AetherState:
    """
    Diagnostica el error:
    1. Busca el error en web (buscar_web + leer primera URL)
    2. LLM analiza error + contexto web + artefacto original
    3. Propone fix concreto
    4. Para código: genera diff legible
    """
    error_msg  = state.get("error_mensaje", "")
    error_ctx  = state.get("error_contexto", "shell")
    orden      = state["orden"]
    mem        = state["mem"]
    cmd_orig   = state.get("error_cmd", "")
    intento    = state.get("error_intento", 0)
    codigo_orig = state.get("error_codigo_original", "")
    max_intentos = state.get("error_max_intentos") or _max_intentos_para(error_ctx)

    # Límite de intentos alcanzado → no seguimos gastando búsquedas/LLM
    if intento >= max_intentos:
        print(f"\n🛑 [DIAGNÓSTICO]: Se alcanzó el límite de {max_intentos} intento(s).")
        state["error_fix_propuesto"] = ""
        state["error_fix_diff"] = ""
        return state

    print(f"\n🔬 [DIAGNÓSTICO] (intento #{intento + 1}/{max_intentos}): Analizando error...")

    # ── Buscar en web ────────────────────────────────────────────────
    contexto_web, fuente_web = _buscar_en_web_fix(error_msg, error_ctx)

    # ── Construir prompt según contexto ─────────────────────────────
    if error_ctx == "codigo":
        prompt_fix = (
            f"El usuario pidió: {orden}\n\n"
            f"Se generó este código:\n```python\n{codigo_orig}\n```\n\n"
            f"Al ejecutarlo con `{cmd_orig}` salió este error:\n```\n{error_msg}\n```\n\n"
            f"Información encontrada en la web:\n{contexto_web}\n\n"
            "Analiza la causa exacta del error y reescribe el código corregido "
            "dentro de un bloque ```python ... ```. Solo el código, sin explicaciones."
        )
    elif error_ctx == "launch":
        prompt_fix = (
            f"El usuario quiso lanzar: {orden}\n\n"
            f"Comando intentado: `{cmd_orig}`\n"
            f"Error: {error_msg}\n\n"
            f"Información encontrada en la web:\n{contexto_web}\n\n"
            "Determina el nombre correcto del ejecutable o flatpak ID y propone "
            "el comando exacto para lanzarlo en formato [SHELL]comando[/SHELL]."
        )
    else:  # shell
        prompt_fix = (
            f"El usuario pidió: {orden}\n\n"
            f"Se ejecutó: `{cmd_orig}`\n\n"
            f"Error:\n```\n{error_msg}\n```\n\n"
            f"Información encontrada en la web:\n{contexto_web}\n\n"
            "Propone el comando corregido en formato [SHELL]comando[/SHELL]. "
            "Solo el comando, sin explicaciones."
        )

    print("\n🤖 [DIAGNÓSTICO]: Generando fix con LLM...")
    fix_raw = _get_llm().invoke(prompt_fix).content

    # ── Extraer fix ──────────────────────────────────────────────────
    fix_propuesto = ""
    fix_diff      = ""

    if error_ctx == "codigo":
        bloques = re.findall(r"```(?:python|bash|sh)?\s*\n(.*?)\n```", fix_raw, re.DOTALL)
        if bloques:
            codigo_nuevo  = bloques[0].strip()
            fix_propuesto = codigo_nuevo
            fix_diff      = _hacer_diff(codigo_orig, codigo_nuevo)
        else:
            # LLM no generó bloque, guardar la respuesta completa para mostrar
            fix_propuesto = fix_raw.strip()
            fix_diff      = ""
    else:
        cmd_fix = extraer_comando_shell_seguro(fix_raw)
        fix_propuesto = cmd_fix or fix_raw.strip()[:400]

    state["error_fix_propuesto"] = fix_propuesto
    state["error_fix_diff"]      = fix_diff
    state["error_fix_fuente"]    = fuente_web
    state["error_intento"]       = intento + 1
    return state


def _despues_de_diagnose(state: AetherState) -> Literal["error_confirm", "error_fallback"]:
    return "error_confirm" if state.get("error_fix_propuesto", "").strip() else "error_fallback"


# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLER — NODO 2: CONFIRM
# Muestra diagnóstico al usuario, siempre pide confirmación
# ══════════════════════════════════════════════════════════════════════

def nodo_error_confirm(state: AetherState) -> AetherState:
    error_ctx     = state.get("error_contexto", "shell")
    error_msg     = state.get("error_mensaje", "")
    fix_propuesto = state.get("error_fix_propuesto", "")
    fix_diff      = state.get("error_fix_diff", "")
    fuente_web    = state.get("error_fix_fuente", "")
    intento       = state.get("error_intento", 1)
    max_intentos  = state.get("error_max_intentos") or _max_intentos_para(error_ctx)
    ya_autorizado = state.get("error_autorizado", False)

    print(f"\n{'═'*55}")
    print(f"🔬 DIAGNÓSTICO DEL ERROR — intento #{intento}/{max_intentos}")
    print(f"{'─'*55}")
    print(f"❌ Error:\n{error_msg[:400]}")
    print(f"{'─'*55}")

    if fuente_web:
        print(f"🌐 Fuente consultada: {fuente_web}")
        print(f"{'─'*55}")

    if error_ctx == "codigo" and fix_diff:
        print("📝 Cambios propuestos (diff):")
        _imprimir_diff(fix_diff)
    else:
        print(f"🔧 Fix propuesto:\n{fix_propuesto}")

    print(f"{'═'*55}")

    # CONFIRMACIÓN ÚNICA: si ya fue autorizado en un intento anterior de este
    # mismo error, no volvemos a preguntar — reintentamos directo hasta el límite.
    if ya_autorizado:
        print(f"✅ [AUTO-FIX]: Ya autorizado al inicio. Aplicando reintento {intento}/{max_intentos} sin volver a preguntar...")
        return state  # → error_retry

    confirmado = _confirmar_usuario(
        f"¿Autorizo a Aether a aplicar este fix y, si vuelve a fallar, "
        f"reintentar por su cuenta hasta {max_intentos} veces sin preguntar de nuevo?"
    )

    if not confirmado:
        print("\n❌ [SISTEMA]: Fix rechazado por el usuario.")
        state["respuesta"] = (
            f"Operación cancelada en el intento #{intento}. "
            "No se aplicó ningún fix."
        )
        state["terminado"]       = True
        state["error_contexto"]  = ""   # limpiar para no re-entrar
        return state

    # Autorización concedida: vale para todos los reintentos siguientes
    state["error_autorizado"] = True
    return state   # continúa → error_retry


def _despues_de_confirm(state: AetherState) -> Literal["error_retry", "fin"]:
    return "fin" if state.get("terminado") else "error_retry"


# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLER — NODO 3: RETRY
# Aplica el fix y ejecuta de nuevo, SIN volver a pedir confirmación
# (ya autorizado en error_confirm). Si vuelve a fallar y quedan intentos
# → reactiva diagnóstico. Si se agota error_max_intentos → diagnose lo detecta
# y manda a fallback.
# ══════════════════════════════════════════════════════════════════════

def nodo_error_retry(state: AetherState) -> AetherState:
    error_ctx     = state.get("error_contexto", "shell")
    fix_propuesto = state.get("error_fix_propuesto", "")
    archivo_cod   = state.get("error_archivo", "")
    mem           = state["mem"]
    orden         = state["orden"]

    if not fix_propuesto.strip():
        state["respuesta"] = "El diagnóstico no generó un fix válido. Operación cancelada."
        state["terminado"] = True
        return state

    if error_ctx == "codigo":
        # Reescribir archivo con el código corregido
        try:
            with open(archivo_cod, "w", encoding="utf-8") as f:
                f.write(fix_propuesto)
        except Exception as e:
            state["respuesta"] = f"No pude escribir el fix en {archivo_cod}: {e}"
            state["terminado"] = True
            return state

        ext = archivo_cod[archivo_cod.rfind("."):]
        cmd = f"python3 {archivo_cod}" if ext == ".py" else f"bash {archivo_cod}"
        print(f"\n⚙️  [RETRY CÓDIGO]: {cmd}")
        salida, hubo_error = ejecutar_comando(cmd)

    elif error_ctx == "launch":
        # fix_propuesto puede venir con [SHELL]...[/SHELL] o directo
        cmd = extraer_comando_shell_seguro(f"[SHELL]{fix_propuesto}[/SHELL]") or fix_propuesto
        cmd = cmd.rstrip() + " &"
        print(f"\n⚙️  [RETRY LAUNCH]: {cmd}")
        salida, hubo_error = ejecutar_comando(cmd)

    else:  # shell
        cmd = fix_propuesto
        print(f"\n⚙️  [RETRY SHELL]: \033[1;33m{cmd}\033[0m")
        salida, hubo_error = ejecutar_comando(cmd)

    print(f"\n{'─'*50}\n{salida}\n{'─'*50}")
    registrar_comando(mem, orden, cmd)

    if hubo_error:
        max_intentos = state.get("error_max_intentos") or _max_intentos_para(error_ctx)
        print(f"\n❌ [RETRY]: Sigue fallando. Volviendo a diagnosticar (intento #{state.get('error_intento', 1) + 1}/{max_intentos})...")
        state["error_mensaje"] = salida
        state["error_cmd"]     = cmd
        # Actualizar código original para que el próximo diff sea incremental
        if error_ctx == "codigo":
            state["error_codigo_original"] = fix_propuesto
        state["error_fix_propuesto"] = ""
        state["error_fix_diff"]      = ""
        return state   # → _despues_de_retry → error_diagnose

    # ── Éxito ────────────────────────────────────────────────────────
    print(f"\n✅ [RETRY]: Fix aplicado con éxito en el intento #{state.get('error_intento', 1)}.")
    respuesta_final = salida or "Operación completada tras corrección automática."
    registrar_turno(mem, "jarvis", respuesta_final)
    state["respuesta"]      = respuesta_final
    state["ya_impreso"]     = True   # FIX #3: nodo_fin no duplicará registro
    state["terminado"]      = True
    state["error_contexto"] = ""   # limpiar
    return state


def _despues_de_retry(state: AetherState) -> Literal["error_diagnose", "fin"]:
    # Si hay error activo (terminado=False y error_contexto presente) → volver a diagnosticar
    if not state.get("terminado") and state.get("error_contexto"):
        return "error_diagnose"
    return "fin"


# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLER — NODO 4: FALLBACK
# Cuando la web no generó un fix → estrategias locales
# ══════════════════════════════════════════════════════════════════════

def nodo_error_fallback(state: AetherState) -> AetherState:
    """
    Estrategias alternativas sin web cuando el diagnóstico no produce fix:
    - launch : fuzzy search en flatpak list completo + whereis
    - shell  : simplificar el comando quitando flags problemáticos
    - codigo : pedir al LLM reescribir con solo stdlib
    """
    error_ctx = state.get("error_contexto", "shell")
    orden     = state["orden"]
    mem       = state["mem"]
    intento   = state.get("error_intento", 0)
    max_intentos = state.get("error_max_intentos") or _max_intentos_para(error_ctx)
    limite_alcanzado = intento >= max_intentos

    if limite_alcanzado:
        print(f"\n🔄 [FALLBACK]: Límite de {max_intentos} intento(s) alcanzado. Probando alternativas locales como última opción...")

    stop_words = frozenset([
        "el", "la", "los", "las", "un", "una", "por", "favor", "me",
        "con", "sin", "en", "de", "del", "al", "ejecuta", "abre",
        "lanza", "inicia", "corre",
    ])
    terminos = [w for w in orden.lower().split() if w not in stop_words and len(w) > 2]

    print(f"\n🔄 [FALLBACK]: Buscando alternativas locales para contexto '{error_ctx}'...")

    if error_ctx == "launch":
        # Fuzzy search en flatpak list completo
        salida_lista, _ = ejecutar_comando(
            "flatpak list --app --columns=application,name 2>/dev/null"
        )
        for linea in salida_lista.splitlines():
            partes = linea.split(None, 1)
            if len(partes) < 2:
                continue
            app_id, nombre = partes
            for t in terminos:
                if t in nombre.lower() or t in app_id.lower():
                    fix = f"flatpak run {app_id}"
                    print(f"\n🔄 [FALLBACK]: Candidato fuzzy encontrado: {nombre} ({app_id})")
                    state["error_fix_propuesto"] = fix
                    state["error_fix_diff"]      = ""
                    state["error_fix_fuente"]    = "(búsqueda local fuzzy en flatpak list)"
                    return state   # → _despues_de_fallback → error_confirm

        # whereis como último recurso
        for t in terminos:
            salida, err = ejecutar_comando(f"whereis {t} 2>/dev/null")
            if not err and ":" in salida:
                rutas = salida.split(":", 1)[1].strip().split()
                if rutas:
                    fix = f"{rutas[0]} &"
                    print(f"\n🔄 [FALLBACK]: whereis encontró: {rutas[0]}")
                    state["error_fix_propuesto"] = fix
                    state["error_fix_diff"]      = ""
                    state["error_fix_fuente"]    = "(whereis local)"
                    return state

    elif error_ctx == "shell":
        # Quitar flags problemáticos del comando
        cmd = state.get("error_cmd", "")
        cmd_simple = re.sub(r"\s+--?\w[\w-]*", "", cmd).strip()
        if cmd_simple and cmd_simple != cmd:
            print(f"\n🔄 [FALLBACK]: Comando simplificado: {cmd_simple}")
            state["error_fix_propuesto"] = cmd_simple
            state["error_fix_diff"]      = ""
            state["error_fix_fuente"]    = "(simplificación local de flags)"
            return state

    elif error_ctx == "codigo":
        # Reescribir sin dependencias externas
        codigo_orig = state.get("error_codigo_original", "")
        print("\n🔄 [FALLBACK]: Solicitando versión con solo stdlib...")
        fix_raw = _get_llm().invoke(
            f"Este código falla, probablemente por dependencias externas:\n"
            f"```python\n{codigo_orig}\n```\n\n"
            "Reescríbelo usando SOLO la librería estándar de Python (sin pip). "
            "Devuelve SOLO el código dentro de ```python ... ```."
        ).content
        bloques = re.findall(r"```python\s*\n(.*?)\n```", fix_raw, re.DOTALL)
        if bloques:
            codigo_nuevo = bloques[0].strip()
            state["error_fix_propuesto"] = codigo_nuevo
            state["error_fix_diff"]      = _hacer_diff(codigo_orig, codigo_nuevo)
            state["error_fix_fuente"]    = "(reescritura stdlib-only)"
            return state

    # Sin alternativas → rendirse
    print("\n🤷 [FALLBACK]: No encontré alternativas.")
    motivo = (
        f"alcanzar el límite de {max_intentos} intento(s)" if limite_alcanzado
        else "no encontrar un diagnóstico válido"
    )
    state["respuesta"] = (
        f"No pude completar la operación tras {motivo}. "
        "No encontré alternativas disponibles."
    )
    state["terminado"]      = True
    state["error_contexto"] = ""
    return state


def _despues_de_fallback(state: AetherState) -> Literal["error_confirm", "fin"]:
    if state.get("terminado"):
        return "fin"
    return "error_confirm"


# ══════════════════════════════════════════════════════════════════════
# NODO: FIN
# ══════════════════════════════════════════════════════════════════════

def nodo_fin(state: AetherState) -> AetherState:
    mem       = state["mem"]
    respuesta = state.get("respuesta")

    # FIX #3 + #9: evitar doble print y doble registro en DB.
    # - "tokens" lo setea nodo_general (streaming token a token, ya imprimió).
    # - "ya_impreso" lo setean nodos que imprimen Y registran ellos mismos
    #   antes de llegar aquí (nodo_analizar_shell, nodo_error_retry, etc.).
    # Cualquier nodo nuevo que agregues: si imprime y registra solo,
    # setea state["ya_impreso"] = True antes de retornar.
    ya_impreso = state.get("ya_impreso", False)
    if respuesta and not state.get("tokens") and not ya_impreso:
        print(f"\n🎙️  Aether: {respuesta}")
    if respuesta and not ya_impreso:
        registrar_turno(mem, "jarvis", respuesta)

    state["terminado"] = True
    return state


# ══════════════════════════════════════════════════════════════════════
# CONSTRUCCIÓN DEL GRAFO
# ══════════════════════════════════════════════════════════════════════

def construir_grafo():
    g = StateGraph(AetherState)

    # ── Nodos principales ────────────────────────────────────────────
    g.add_node("router",          nodo_router)
    g.add_node("memoria",         nodo_memoria)
    g.add_node("vision",          nodo_vision)
    g.add_node("lanzar_app",      nodo_lanzar_app)
    g.add_node("web",             nodo_web)
    g.add_node("general",         nodo_general)
    g.add_node("shell",           nodo_shell)
    g.add_node("analizar_shell",  nodo_analizar_shell)
    g.add_node("escribir_archivo",nodo_escribir_archivo)
    g.add_node("fin",             nodo_fin)

    # ── Nodos del error handler ──────────────────────────────────────
    g.add_node("error_diagnose",  nodo_error_diagnose)
    g.add_node("error_confirm",   nodo_error_confirm)
    g.add_node("error_retry",     nodo_error_retry)
    g.add_node("error_fallback",  nodo_error_fallback)

    # ── Edges ────────────────────────────────────────────────────────
    g.add_edge(START, "router")

    g.add_conditional_edges("router", _despues_de_router, {
        "memoria":    "memoria",
        "vision":     "vision",
        "lanzar_app": "lanzar_app",
        "web":        "web",
        "general":    "general",
    })

    g.add_conditional_edges("lanzar_app", _despues_de_lanzar, {
        "fin":            "fin",
        "general":        "general",
        "error_diagnose": "error_diagnose",
    })

    g.add_conditional_edges("general", _despues_de_general, {
        "shell":   "shell",
        "escribir":"escribir_archivo",
        "fin":     "fin",
    })

    g.add_conditional_edges("shell", _despues_de_shell, {
        "analizar":       "analizar_shell",
        "error_diagnose": "error_diagnose",
        "fin":            "fin",
    })

    g.add_conditional_edges("escribir_archivo", _despues_de_escribir, {
        "error_diagnose": "error_diagnose",
        "fin":            "fin",
    })

    # Error handler loop
    g.add_conditional_edges("error_diagnose", _despues_de_diagnose, {
        "error_confirm":  "error_confirm",
        "error_fallback": "error_fallback",
    })

    g.add_conditional_edges("error_confirm", _despues_de_confirm, {
        "error_retry": "error_retry",
        "fin":         "fin",
    })

    g.add_conditional_edges("error_retry", _despues_de_retry, {
        "error_diagnose": "error_diagnose",
        "fin":            "fin",
    })

    g.add_conditional_edges("error_fallback", _despues_de_fallback, {
        "error_confirm": "error_confirm",
        "fin":           "fin",
    })

    # Nodos que terminan directo en END
    g.add_edge("memoria",        END)
    g.add_edge("vision",         END)
    g.add_edge("web",            END)
    g.add_edge("analizar_shell", END)
    g.add_edge("fin",            END)

    return g.compile()


# ══════════════════════════════════════════════════════════════════════
# SINGLETON + API PÚBLICA
# ══════════════════════════════════════════════════════════════════════

_grafo_compilado = None


def get_grafo():
    global _grafo_compilado
    if _grafo_compilado is None:
        _grafo_compilado = construir_grafo()
    return _grafo_compilado


def procesar_orden_completo(orden: str, mem: dict, modo_autonomo: bool = True) -> str | None:
    grafo = get_grafo()
    estado_inicial: AetherState = {
        "orden":                 orden,
        "mem":                   mem,
        "modo_autonomo":         modo_autonomo,
        "tokens":                [],
        "terminado":             False,
        "ruta":                  "",
        "respuesta":             None,
        "comando_shell":         None,
        "salida_shell":          "",
        "hubo_error":            False,
        "error_contexto":        "",
        "error_mensaje":         "",
        "error_cmd":             "",
        "error_codigo_original": "",
        "error_archivo":         "",
        "error_intento":         0,
        "error_max_intentos":    0,
        "error_autorizado":      False,
        "error_fix_propuesto":   "",
        "error_fix_diff":        "",
        "error_fix_fuente":      "",
        "ya_impreso":            False,   # FIX #3: flag para nodo_fin
    }
    estado_final = grafo.invoke(estado_inicial)
    return estado_final.get("respuesta")