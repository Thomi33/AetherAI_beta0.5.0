"""
Nodos del grafo LangGraph para Aether.

Cada función recibe AetherState y retorna un dict parcial con solo
los campos que modifica. LangGraph hace el merge automáticamente.

REFACTOR: Tools como proveedores de datos puros
------------------------------------------------
Los nodos tool (web, shell, vision, codigo, launch, memory) ya NO
sintetizan respuesta con LLM internamente. Solo ejecutan su acción,
guardan los datos crudos en el estado y retornan.

La síntesis final SIEMPRE la hace node_plan_synthesizer (o node_text
para el caso de conversación directa). Esto permite que Ornith vea
todos los datos crudos y razone sobre ellos antes de responder.

Excepciones mantenidas:
- node_text: sigue llamando al LLM (es puramente conversacional, no
  es una "tool" de datos).
- node_error_diagnose / node_error_fallback: siguen usando LLM porque
  el error handler necesita razonar para proponer un fix.

Nodos principales
-----------------
node_planner        — detecta intención y arma el plan de ejecución
node_plan_executor  — ejecuta pasos del plan reutilizando nodos reales
node_plan_synthesizer — Ornith sintetiza TODOS los resultados del plan
node_web            — buscar_web() → datos crudos en web_results
node_shell          — LLM genera comando → ejecutar_comando() → datos crudos
node_launch         — lanza programas (flatpak / PATH)
node_vision         — captura pantalla → ver_pantalla() → datos crudos
node_codigo         — LLM genera código → ejecuta → datos crudos
node_text           — respuesta directa via ollama streaming (sin tool)
node_memory         — procesa comandos de memoria sin LLM
node_finalize       — registra respuesta en DB, marca done=True

Nodos del error handler
-----------------------
node_error_diagnose — LLM diagnostica el error + busca en web
node_error_confirm  — muestra diagnóstico al usuario y pide confirmación
node_error_retry    — ejecuta el fix propuesto
node_error_fallback — estrategia alternativa sin web (PATH, variantes)
"""

import re
import difflib
import shlex
import unicodedata
import time
import ollama

from langchain_core.messages import HumanMessage, AIMessage

from core.config.settings import MODELO, NUM_CTX, OLLAMA_GEN_OPTIONS, OLLAMA_KEEP_ALIVE, BENCH_INSTRUMENT, BENCH_LOG_PATH
from core.memory.memory_manager import registrar_turno, registrar_comando, normalizar_mem
from core.memory.context_builder import construir_contexto_memoria
from core.agent.prompts import construir_backstory
from core.tools.shell_executor import ejecutar_comando
from core.tools.web_search import buscar_web
from core.tools.url_reader import leer_url
from core.tools.vision import ver_pantalla
from core.tools.flatpak_manager import buscar_flatpak_en_memoria, actualizar_flatpaks
from core.parser.shell_parser import extraer_comando_shell
from core.tools.file_writer import escribir_archivo
from core.events import get_event_bus, Event
from core.config import get_config_manager

from core.agent.graph_state import AetherState


# ══════════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ══════════════════════════════════════════════════════════════════════

def _emitir_evento(type: str, payload: dict) -> None:
    """Helper para emitir eventos al EventBus."""
    try:
        bus = get_event_bus()
        bus.publish(Event(type=type, payload=payload))
    except Exception:
        pass  # No romper si EventBus no está inicializado


def _print_event(type: str, message: str, **kwargs) -> None:
    """
    Print que también emite un evento.
    Mantiene compatibilidad con prints existentes.
    """
    _emitir_evento(type, {"message": message, **kwargs})
    print(message)


def _llm_chat(system: str = None, user: str = None, messages: list = None, on_token=None, tools: list = None) -> str:
    """
    Llamada directa a ollama con streaming opcional.

    Siempre usa el modo Ornith-native:
    - messages list (para el chat_template)
    - sampling recomendado (0.6 / 0.95 / 20)
    - keep_alive
    - num_predict generoso
    """
    if messages is None:
        if system is None or user is None:
            raise ValueError("_llm_chat requiere o 'messages' o (system + user)")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    opts = {
        "num_ctx": NUM_CTX,
        "num_predict": 2048,
    }
    opts.update(OLLAMA_GEN_OPTIONS)

    call_kwargs = {
        "model": MODELO,
        "messages": messages,
        "stream": True,
        "options": opts,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }
    if tools:
        call_kwargs["tools"] = tools

    start_time = time.time() if BENCH_INSTRUMENT else 0
    prompt_chars = sum(len(str(m.get("content", ""))) for m in messages) if BENCH_INSTRUMENT else 0

    respuesta = ""
    for chunk in ollama.chat(**call_kwargs):
        msg = chunk.get("message", {})
        token = msg.get("content", "")
        if token and on_token:
            on_token(token)
        respuesta += token

    if BENCH_INSTRUMENT:
        duration_ms = int((time.time() - start_time) * 1000)
        output_chars = len(respuesta)
        has_think = "<think>" in respuesta
        try:
            with open(BENCH_LOG_PATH, "a") as f:
                import json as _json
                f.write(_json.dumps({
                    "ts": time.time(),
                    "mode": "ornith-native",
                    "prompt_chars": prompt_chars,
                    "output_chars": output_chars,
                    "duration_ms": duration_ms,
                    "has_think": has_think,
                    "num_messages": len(messages),
                }) + "\n")
        except Exception:
            pass  # never break normal operation

    return respuesta


def _parse_ornith_thinking(text: str) -> tuple[str, str]:
    """
    Ornith-1.0 native format handler.

    Returns (reasoning, clean_content).

    Improvements for robustness:
    - Handles cases where final content is JSON or [SHELL] after </think>.
    - Tries to extract the last sensible payload if thinking pollutes.
    """
    if not isinstance(text, str) or "</think>" not in text:
        return "", (text or "").strip()

    parts = text.split("</think>", 1)
    reasoning = parts[0].replace("<think>", "").strip()
    rest = parts[1].lstrip("\n").strip() if len(parts) > 1 else ""

    # Try to recover if rest is empty but there's more content
    if not rest:
        rest = text

    return reasoning, rest


def _system_prompt(mem: dict, state: "AetherState | None" = None) -> str:
    """
    Construye el system prompt.
    Si el Context Manager ya preparó context_slots, los usa directamente.
    Si no (fallback), construye el contexto desde cero con el tema vacío.
    """
    from core.memory.context_builder import construir_contexto_memoria
    from core.agent.prompts import construir_backstory

    if state is not None:
        slots = state.get("context_slots") or {}
        contexto = slots.get("contexto", "")
        if contexto:
            return construir_backstory(contexto)

    contexto = construir_contexto_memoria(mem, tema="")
    return construir_backstory(contexto)


def _system_prompt_sintesis(mem: dict, state: "AetherState | None" = None) -> str:
    """
    System prompt para node_plan_synthesizer: reporta resultados, no ejecuta.

    A diferencia de _system_prompt() (usado por node_shell/node_codigo para
    GENERAR comandos vía protocolo [SHELL]), este usa construir_persona_sintesis,
    que no contiene el protocolo [SHELL] en absoluto. Evita que Ornith devuelva
    comandos crudos cuando debería sintetizar una respuesta en lenguaje natural.
    """
    from core.memory.context_builder import construir_contexto_memoria
    from core.agent.prompts import construir_persona_sintesis

    if state is not None:
        slots = state.get("context_slots") or {}
        contexto = slots.get("contexto", "")
        if contexto:
            return construir_persona_sintesis(contexto)

    contexto = construir_contexto_memoria(mem, tema="")
    return construir_persona_sintesis(contexto)


def _confirmar_usuario(mensaje: str) -> bool:
    """Muestra mensaje al usuario y retorna True si confirma."""
    print(f"\n{mensaje}")
    resp = input("¿Autorizar? [S/n]: ").strip().lower()
    return resp in ("", "s", "si", "y", "yes")


# ══════════════════════════════════════════════════════════════════════
# PALABRAS CLAVE (espejo de aether_service para el router)
# ══════════════════════════════════════════════════════════════════════

def _normalizar(texto: str) -> str:
    """
    Normaliza texto para matching de keywords robusto:
    - minúsculas
    - elimina diacríticos (tildes/diéresis): 'muéstrame' → 'muestrame'
    """
    if not isinstance(texto, str):
        return ""
    texto = texto.lower()
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


_KW_CODIGO = re.compile(
    r"\b(escribe|crea|genera|programa|script|funcion|clase|implementa"
    r"|codigo|python|java|bash|html|css|javascript)\b",
    re.IGNORECASE,
)

_STOP_WORDS = frozenset([
    "el", "la", "los", "las", "un", "una", "por", "favor", "me",
    "con", "sin", "en", "de", "del", "al",
])


# ══════════════════════════════════════════════════════════════════════
# KEYWORDS EDITABLES (cargadas desde JSON, sin tocar código Python)
# ══════════════════════════════════════════════════════════════════════

import json as _json
import os as _os

_KEYWORDS_CONFIG_PATH = _os.path.join(_os.path.dirname(__file__), "keywords_config.json")


def _cargar_keywords_config() -> dict:
    """
    Carga keywords_config.json. Si falta o está corrupto, usa un fallback
    mínimo en código para que el sistema no caiga por completo — pero
    imprime una advertencia bien visible, porque ese fallback es deliberadamente
    pobre (el archivo JSON es la fuente de verdad real).
    """
    try:
        with open(_KEYWORDS_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = _json.load(f)
        cfg.pop("_comentario", None)
        return cfg
    except Exception as e:
        print(f"\n⚠️  [PLANNER]: No se pudo cargar {_KEYWORDS_CONFIG_PATH} ({e}). "
              f"Usando keywords mínimas de emergencia.")
        return {
            "web": ["busca", "que es", "qué es"],
            "vision": ["captura de pantalla"],
            "launch": ["ejecuta", "abre", "lanza", "inicia", "corre"],
            "launch_excluye": ["comando", "caché", "cache"],
            "memory": ["tu memoria", "mi memoria"],
            "anaforico": ["ejecutalo", "hacelo", "dale"],
        }


_KEYWORDS = _cargar_keywords_config()

_KW_WEB_N            = frozenset(_normalizar(k) for k in _KEYWORDS.get("web", []))
_KW_VISION_N         = frozenset(_normalizar(k) for k in _KEYWORDS.get("vision", []))
_KW_LAUNCH_N         = frozenset(_normalizar(k) for k in _KEYWORDS.get("launch", []))
_KW_LAUNCH_EXCLUYE_N = frozenset(_normalizar(k) for k in _KEYWORDS.get("launch_excluye", []))
_KW_MEMORY_N         = frozenset(_normalizar(k) for k in _KEYWORDS.get("memory", []))
_KW_ANAFORICO_N      = frozenset(_normalizar(k) for k in _KEYWORDS.get("anaforico", []))
_KW_MCP_N = frozenset(["usando mcp", "via mcp", "con mcp", "model context protocol", "mcp"])

def _inferir_args_mcp(orden: str, manager) -> dict:
    """
    Infiere server/name/arguments MCP de forma GENÉRICA usando:
    1. Catálogo real de TODOS los servers configurados
    2. LLM para elegir la tool más adecuada y sus argumentos
    3. Validación de argumentos requeridos + valores por defecto
    4. Fallback heurístico si el LLM falla
    """
    import json

    # 1. Obtener catálogo real
    try:
        catalogo = manager.list_all_tools()  # {server: [tools...]}
    except Exception as e:
        print(f"   └─ ️  [MCP]: no se pudo obtener catálogo ({e})")
        return {"server": "", "name": "", "arguments": {}}

    if not catalogo or all(not tools for tools in catalogo.values()):
        print(f"   └─ ⚠️  [MCP]: no hay servers/tools disponibles")
        return {"server": "", "name": "", "arguments": {}}

    # 2. Construir catálogo condensado para el LLM
    lineas_catalogo = []
    for server, tools in catalogo.items():
        for t in tools:
            name = t.get("name", "unknown")
            desc = t.get("description", "").replace("\n", " ")[:150]
            schema = t.get("input_schema", {})
            required = schema.get("required", [])
            args_info = ", ".join(required) if required else "ninguno"
            lineas_catalogo.append(
                f"  - Server '{server}', tool '{name}': {desc} | Args requeridos: {args_info}"
            )

    catalogo_str = "\n".join(lineas_catalogo)

    # 3. Pedir al LLM que elija la tool y argumentos
    prompt = f"""El usuario pidió: "{orden}"

Herramientas MCP disponibles:
{catalogo_str}

Respondé SOLO con un JSON válido en este formato exacto (sin explicaciones, sin markdown):
{{"server": "nombre_del_server", "name": "nombre_de_la_tool", "arguments": {{"arg1": "valor1"}}}}

Reglas CRÍTICAS:
- Elegí la tool más adecuada para la orden del usuario
- Completá TODOS los argumentos requeridos con valores razonables y específicos
- Para filesystem: usá paths reales como /home/thomi (NO dejes vacío)
- Para búsquedas: usá el tema específico de la orden (NO términos genéricos)
- Si no hay una tool obvia, respondé {{"server": "", "name": "", "arguments": {{}}}}"""

    try:
        raw = _llm_chat(
            system="Eres un selector de herramientas MCP experto. Respondé SOLO JSON válido.",
            user=prompt
        )
        _, contenido = _parse_ornith_thinking(raw)

        # Limpiar markdown
        if "```json" in contenido:
            contenido = contenido.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in contenido:
            contenido = contenido.split("```", 1)[1].split("```", 1)[0].strip()

        data = json.loads(contenido)

        # Validar estructura
        if isinstance(data, dict):
            server = data.get("server", "")
            name = data.get("name", "")
            arguments = data.get("arguments", {})

            if not server and not name:
                return {"server": "", "name": "", "arguments": {}}

            if server in catalogo:
                tools_del_server = catalogo[server]
                tool_obj = next((t for t in tools_del_server if t.get("name") == name), None)

                if tool_obj:
                    # VALIDACIÓN DE ARGUMENTOS REQUERIDOS
                    schema = tool_obj.get("input_schema", {})
                    required_args = schema.get("required", [])
                    properties = schema.get("properties", {})

                    # Completar argumentos faltantes con valores por defecto
                    for arg_name in required_args:
                        if arg_name not in arguments or not arguments[arg_name]:
                            if arg_name == "path":
                                arguments[arg_name] = "/home/thomi"
                            elif arg_name == "query":
                                arguments[arg_name] = orden.lower().replace("usando mcp", "").strip()
                            elif arg_name == "sql":
                                arguments[arg_name] = "SELECT 1"
                            else:
                                arguments[arg_name] = ""
                                
                    arguments = _normalizar_args_mcp(server, name, arguments, orden)
                    print(f"   └─ [MCP]: LLM eligió server='{server}', tool='{name}'")
                    print(f"   └─ [MCP]: Args finales (validados): {arguments}")
                    return {"server": server, "name": name, "arguments": arguments}
    except Exception as e:
        print(f"   └─ ⚠️  [MCP]: LLM falló al inferir args ({e})")

    # 4. Fallback heurístico robusto
    orden_lower = orden.lower()

    # Filesystem
    if "server_filesystem" in catalogo:
        if any(kw in orden_lower for kw in ["directorio", "lista", "ls", "archivos"]):
            return {"server": "server_filesystem", "name": "list_directory", "arguments": {"path": "/home/thomi"}}
        if any(kw in orden_lower for kw in ["leer", "cat", "ver archivo"]):
            return {"server": "server_filesystem", "name": "read_text_file", "arguments": {"path": "/home/thomi"}}

    # GitHub
    if "github" in catalogo:
        if any(kw in orden_lower for kw in ["github", "repositorio", "repo"]):
            args = _normalizar_args_mcp("github", "search_repositories", {"query": orden_lower}, orden)
            return {"server": "github", "name": "search_repositories", "arguments": args}
            
    # Notion
    if "notion" in catalogo:
        if any(kw in orden_lower for kw in ["notion", "nota"]):
            query_raw = orden_lower.replace("notion", "").replace("nota", "").replace("busca", "").replace("en", "")
            query = query_raw.strip()
            if not query:
                query = "hoy"
            return {"server": "notion", "name": "API-post-search", "arguments": {"query": query}}

    print(f"   ─ ⚠️  [MCP]: fallback no pudo inferir args para '{orden_lower}'")
    return {"server": "", "name": "", "arguments": {}}

def recargar_keywords_config() -> None:
    """
    Recarga keywords_config.json en caliente, sin reiniciar el proceso.
    Útil si ajustás el JSON mientras Aether está corriendo (TUI persistente).
    """
    global _KEYWORDS, _KW_WEB_N, _KW_VISION_N, _KW_LAUNCH_N, _KW_LAUNCH_EXCLUYE_N, _KW_MEMORY_N, _KW_ANAFORICO_N, _KW_MCP_N
    _KEYWORDS = _cargar_keywords_config()
    _KW_WEB_N            = frozenset(_normalizar(k) for k in _KEYWORDS.get("web", []))
    _KW_VISION_N         = frozenset(_normalizar(k) for k in _KEYWORDS.get("vision", []))
    _KW_LAUNCH_N         = frozenset(_normalizar(k) for k in _KEYWORDS.get("launch", []))
    _KW_LAUNCH_EXCLUYE_N = frozenset(_normalizar(k) for k in _KEYWORDS.get("launch_excluye", []))
    _KW_MEMORY_N         = frozenset(_normalizar(k) for k in _KEYWORDS.get("memory", []))
    _KW_ANAFORICO_N      = frozenset(_normalizar(k) for k in _KEYWORDS.get("anaforico", []))
    _KW_MCP_N = frozenset(["usando mcp", "via mcp", "con mcp", "model context protocol", "mcp"])
    print("🔄 [PLANNER]: keywords_config.json recargado.")


# ══════════════════════════════════════════════════════════════════════
# NODO: PLANNER (Tool Planning)
# ══════════════════════════════════════════════════════════════════════

def _detectar_intent_keywords(orden_lower: str) -> str | None:
    """
    Detección determinista de intención por keywords (sin LLM).
    Prioridad: memory → vision → launch → web → codigo.
    Retorna el nombre de la tool o None si no hay match.
    """
    o = _normalizar(orden_lower)

    def _match_palabra(keywords: frozenset[str]) -> bool:
        # Multi-palabra ("tu memoria"): matching directo por substring tiene sentido.
        # Una sola palabra ("ejecuta"): exigir límite de palabra para no matchear
        # dentro de otra palabra (ej. "ejecutálo" no debe activar "ejecuta").
        return any(
            re.search(rf"\b{re.escape(x)}\b", o) if " " not in x else x in o
            for x in keywords
        )

    if _match_palabra(_KW_MEMORY_N):
        return "memory"
    if _match_palabra(_KW_VISION_N) and not _match_palabra(_KW_WEB_N):
        return "vision"
    if _match_palabra(_KW_LAUNCH_N) and not _match_palabra(_KW_LAUNCH_EXCLUYE_N):
        return "launch"
    if _match_palabra(_KW_WEB_N):
        return "web"
    if _KW_CODIGO.search(o):
        return "codigo"
    return None


_CONECTORES_MULTITOOL = (
    " y luego", " luego", " después", " despues", " entonces",
    " y guarda", " y guárdalo", " y guardalo", " y crea", " y escribe", ";",
)
_KW_PERSISTENCIA = (
    "guarda", "guárdalo", "guardalo", "guardar", "archivo", "escribe en",
    "crea un archivo", "guárdala", "guardala",
)


def _parece_multitool(orden_lower: str) -> bool:
    """
    Heurística determinista para decidir si vale la pena pedirle al LLM un
    plan multi-tool.
    """
    o = _normalizar(orden_lower)
    tiene_conector = any(_normalizar(c) in o for c in _CONECTORES_MULTITOOL)
    if not tiene_conector:
        return False

    categorias = 0
    if any(x in o for x in _KW_WEB_N):
        categorias += 1
    if any(x in o for x in _KW_LAUNCH_N):
        categorias += 1
    if any(x in o for x in _KW_VISION_N):
        categorias += 1
    if _KW_CODIGO.search(o):
        categorias += 1
    if any(_normalizar(p) in o for p in _KW_PERSISTENCIA):
        categorias += 1
    return categorias >= 2


def _extraer_json_objeto(texto: str):
    """
    Extrae y parsea el PRIMER objeto JSON {...} balanceado en un texto.
    Útil cuando el LLM antepone prosa al JSON.
    """
    import json
    if not isinstance(texto, str):
        return None
    inicio = texto.find("{")
    if inicio == -1:
        return None

    profundidad = 0
    en_cadena = False
    escape = False
    for i in range(inicio, len(texto)):
        ch = texto[i]
        if en_cadena:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                en_cadena = False
            continue
        if ch == '"':
            en_cadena = True
        elif ch == "{":
            profundidad += 1
        elif ch == "}":
            profundidad -= 1
            if profundidad == 0:
                try:
                    return json.loads(texto[inicio:i + 1])
                except Exception:
                    return None
    return None

def obtener_catalogo_mcp_condensado(manager) -> str:
    """
    Consume manager.list_all_tools() y devuelve un string compacto
    con el catálogo de herramientas MCP disponibles para el planner.
    """
    try:
        catalogo = manager.list_all_tools()  # dict[str, list[dict]]
    except Exception as e:
        return f"Error al cargar catálogo MCP: {str(e)}"
    
    if not catalogo:
        return "No hay servidores o herramientas MCP activas en este momento."
    
    lineas = []
    for server, tools in catalogo.items():
        if not tools:
            continue
        for t in tools:
            name = t.get("name", "unknown")
            desc = t.get("description", "Sin descripción").replace("\n", " ")
            desc_corta = desc if len(desc) < 120 else desc[:117] + "..."
            schema = t.get("input_schema", {})
            required_args = schema.get("required", [])
            args_str = f" (Req: {', '.join(required_args)})" if required_args else ""
            lineas.append(f"- Server '{server}': herramienta '{name}' -> {desc_corta}{args_str}")
    
    if not lineas:
        return "No hay servidores o herramientas MCP activas en este momento."
    
    return "\n".join(lineas)

def _normalizar_args_mcp(server: str, name: str, arguments: dict, orden: str) -> dict:
    """
    Normaliza argumentos para tools MCP conocidas cuya API tiene
    sintaxis propia que un LLM genérico no maneja bien.
    Corre SIEMPRE (venga del LLM o del fallback) para evitar
    que basten "argumentos parseables" pero semánticamente inútiles.
    """
    if server == "github" and name == "search_repositories":
        query = arguments.get("query", "").strip()

        # Sacar ruido en lenguaje natural que GitHub Search no entiende
        ruido = ["most popular", "más populares", "populares", "the", "los", "las",
                 "repositorios", "repositories", "de", "sobre"]
        query_limpia = query.lower()
        for kw in ruido:
            query_limpia = query_limpia.replace(kw, " ")
        query_limpia = " ".join(query_limpia.split()).strip()

        # Si después de limpiar no queda tema (o el query original era genérico),
        # usar solo qualifiers — GitHub permite buscar SOLO con stars:>N
        if not query_limpia:
            query_limpia = ""

        # Forzar qualifier de estrellas si no está ya presente
        if "stars:" not in query_limpia:
            query_limpia = f"{query_limpia} stars:>1000".strip()

        arguments["query"] = query_limpia
        arguments.setdefault("sort", "stars")
        arguments.setdefault("order", "desc")

    # Acá podés ir agregando más casos: notion, postgres, etc.

    return arguments


def _planner_llm(orden: str, mem: dict) -> list[dict] | None:
    """
    Pide al LLM un plan multi-tool. Retorna lista de pasos normalizados
    al contrato {"tool","instruccion","args"} o None si falla.
    """
    import json
    from core.tools.mcp_client import mcp_manager  # <--- Importas tu manager instanciado
    # Generas el string condensado
    mcp_catalog = obtener_catalogo_mcp_condensado(mcp_manager)
    
    # Detectar si es una orden MCP single-tool y generar args automáticamente
    orden_lower = orden.lower()
    if any(kw in _normalizar(orden_lower) for kw in _KW_MCP_N):
        # El usuario pidió MCP explícitamente - intentar inferir server/tool
        # Para "listame directorios", usar filesystem:list_directory
        _print_event("planner_mcp_detected", f"Orden MCP detectada, generando args para filesystem:list_directory")
        return [{
            "tool": "mcp",
            "instruccion": orden,
            "args": {
                "server": "server_filesystem",
                "name": "list_directory",
                "arguments": {"path": "/home/thomi"}
            }
        }]
    
    prompt = f"""Analiza si esta tarea requiere VARIAS herramientas encadenadas.
Herramientas disponibles:
web: búsqueda en internet
shell: ejecutar comandos de sistema (NO usar para guardar archivos, NI para listar o leer directorios/archivos si hay un servidor MCP disponible para ello)
launch: abrir aplicaciones
vision: capturar/analizar pantalla
codigo: generar y ejecutar código
file_write: guardar el resultado del paso anterior en un archivo (usar SIEMPRE que el usuario pida guardar/escribir en archivo)
mcp: invocar herramientas externas. IMPORTANTE: Si necesitas usar cualquiera de las herramientas MCP listadas abajo, DEBES poner en el campo tool la palabra exacta "mcp" (NO el nombre del servidor) y estructurar los args de esta manera exacta: {{"server": "nombre_del_servidor", "name": "nombre_de_la_tool", "arguments": {{...}}}}
Servidores y herramientas MCP activas actualmente:
{mcp_catalog}
text: respuesta directa
IMPORTANTE: Para guardar resultados en archivo usa SIEMPRE "file_write", NUNCA "shell" con echo/tee.
El nodo file_write toma automáticamente el resultado del paso anterior, no necesitas especificar el contenido.
Tarea: {orden}
Si necesita UNA sola herramienta, responde: {{"multi_tool": false}}
Si necesita VARIAS en secuencia, responde EXACTAMENTE este formato:
{{
 "multi_tool ": true,
 "pasos ": [
{{ "tool ":  "web ",  "instruccion ":  "buscar el precio de X ",  "args ": {{ "query ":  "precio X "}}}},
{{ "tool ":  "file_write ",  "instruccion ":  "guardar el resultado en un archivo ",  "args ": {{ "filename ":  "precio_x.txt "}}}}
]
}}
Responde SOLO el JSON, sin explicaciones."""

    raw = ""
    try:
        raw = _llm_chat(system=_system_prompt(mem), user=prompt).strip()
    except Exception as e:
        print(f"   └─ [PLANNER]: fallo al invocar al LLM ({e})")
        return None

    # Always clean <think> (Ornith reasoning) before trying to parse structured output.
    _, contenido = _parse_ornith_thinking(raw)

    if "```json" in contenido:
        contenido = contenido.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in contenido:
        contenido = contenido.split("```", 1)[1].split("```", 1)[0].strip()

    data = None
    try:
        data = json.loads(contenido)
    except Exception:
        data = _extraer_json_objeto(contenido)

    if data is None:
        preview = raw[:300].replace("\n", " ") if raw else "<vacío>"
        print(
            "   └─ [PLANNER]: la respuesta del LLM no es JSON válido. "
            f"Contenido recibido (truncado): {preview!r}"
        )
        return None

    if not isinstance(data, dict):
        print(f"   └─ [PLANNER]: el JSON del LLM no es un objeto (tipo {type(data).__name__}).")
        return None

    if not data.get("multi_tool") or not isinstance(data.get("pasos"), list):
        return None

    pasos_norm: list[dict] = []
    for paso in data["pasos"]:
        if not isinstance(paso, dict):
            continue
        args = paso.get("args") if isinstance(paso.get("args"), dict) else {}
        instruccion = paso.get("instruccion")
        if not (isinstance(instruccion, str) and instruccion.strip()):
            instruccion = (
                args.get("query") or args.get("command") or args.get("app") or orden
            )
        pasos_norm.append({
            "tool": paso.get("tool"),
            "instruccion": instruccion,
            "args": args,
        })

    return pasos_norm or None


def _posible_referencia_anaforica(orden_lower: str) -> bool:
    """
    Filtro RÁPIDO sin LLM (etapa 1 de resolución de ambigüedad anafórica).

    Solo descarta los casos obviamente NO anafóricos usando keywords
    de "anaforico" en keywords_config.json. Su objetivo es EVITAR gastar
    una llamada al LLM local en el 95%+ de los turnos que no usan "dale",
    "ejecutalo", etc.

    Si este filtro da True, recién entonces se invoca el LLM para confirmar
    semánticamente si la orden depende del turno anterior o ya es autosuficiente.

    Esto soluciona el problema histórico de keywords puros: una frase como
    "Buscá el comando X y ejecutálo" contiene la keyword pero NO depende
    del contexto previo (el QUÉ ya está especificado).
    """
    if not orden_lower or not _KW_ANAFORICO_N:
        return False
    o = _normalizar(orden_lower)
    # Multi-palabra: substring match (ej "el de antes").
    # Una palabra: límite de palabra \b para no matchear dentro de "ejecutálo".
    return any(
        re.search(rf"\b{re.escape(x)}\b", o) if " " not in x else x in o
        for x in _KW_ANAFORICO_N
    )


def _es_referencia_anaforica(orden_lower: str) -> bool:
    """Alias de compatibilidad hacia atrás para _posible_referencia_anaforica."""
    return _posible_referencia_anaforica(orden_lower)


def _confirmar_referencia_anaforica_llm(orden: str) -> bool:
    """
    Etapa 2 (solo si el filtro rápido dio positivo): pregunta al LLM local
    (Ornith) si la orden DEPENDE del turno anterior para saber qué acción
    concreta realizar, o si ya es autosuficiente.

    Devuelve True solo si el modelo responde afirmativamente que "necesita
    el contexto previo".

    Prompt endurecido con ejemplos few-shot para casos límite:
    - "dale, hacelo de nuevo", "ejecutalo" → si (anafórica pura)
    - "dale doble click...", "Buscá X y ejecutálo" → no (autosuficiente aunque
      contenga muletilla)

    Esto es más robusto que cualquier heurística de keywords porque el LLM
    entiende la estructura semántica completa de la orden.
    """
    system = (
        "Eres un detector binario extremadamente preciso de referencias anafóricas "
        "en español conversacional. Respondés SOLO 'si' o 'no' en minúsculas, sin "
        "explicaciones ni puntuación adicional."
    )

    # Few-shot examples elegidos para los casos problemáticos reportados.
    # Incluye el bug que mató dos implementaciones previas de keywords.
    user = f"""Analizá si la ORDEN del usuario depende del TURNO ANTERIOR para saber
exactamente QUÉ hacer o a qué se refiere.

Criterios:
- 'si' → la orden es corta, vaga o usa muletilla de confirmación Y NO especifica
         por sí sola el objeto/acción concreta (necesita el contexto del turno
         anterior de Aether).
- 'no' → aunque aparezcan palabras como 'dale', 'ejecuta', 'hacelo', 'guardalo',
         la frase YA contiene toda la información necesaria para actuar (es
         autosuficiente). "dale" puede ser parte de una acción (imperativo), no
         necesariamente confirmación.

Ejemplos:

Orden: ejecutalo
si

Orden: dale
si

Orden: hacelo de nuevo
si

Orden: dale nomás
si

Orden: si, ejecutalo
si

Orden: dale doble click al ícono de la papelera
no

Orden: Buscá el comando para limpiar la caché de pip y ejecutálo
no

Orden: escribí un script que limpie pip y ejecutalo
no

Orden: guardalo en resultado.txt
no   (porque la orden ya dice qué guardar y dónde)

Orden: guardalo
si   (si hay algo previo que guardar)

Orden: mandaselo
si

Orden: mandaselo a juan
no

Orden actual: {orden}

¿La orden depende del turno anterior para saber qué hacer exactamente?
Respuesta:"""

    try:
        raw = _llm_chat(system=system, user=user).strip().lower()
    except Exception as e:
        print(f"   └─ [PLANNER]: _confirmar_referencia_anaforica_llm falló ({e}); default 'no' para no bloquear.")
        return False

    # Aceptamos varias formas robustas que el modelo puede devolver.
    if raw.startswith("si") or raw.startswith("sí") or "si" == raw or "yes" in raw:
        return True
    # Cualquier otra cosa (incluido "no", basura, explicaciones) → tratamos como no anafórica
    # para no quedarnos en un bucle de aclaraciones.
    return False


def _extraer_comando_de_texto(texto: str) -> str | None:
    """
    Busca un comando ejecutable en un texto libre (típicamente el último
    turno de Aether), en cualquiera de los formatos que el sistema usa:
    [SHELL]...[/SHELL], backticks simples `cmd`, o bloque ```bash...```.
    Retorna el primer comando encontrado, o None si no hay ninguno.
    """
    if not texto:
        return None

    m = re.search(r"\[SHELL\](.*?)\[/SHELL\]", texto, re.DOTALL)
    if m:
        return m.group(1).strip()

    m = re.search(r"```(?:bash|sh|zsh)?\n?(.*?)\n?```", texto, re.DOTALL)
    if m and m.group(1).strip():
        return m.group(1).strip()

    # Backtick simple: `pip cache purge` — el más común cuando Ornith
    # propone un comando en prosa conversacional (no en modo ejecución).
    m = re.search(r"`([^`\n]{3,200})`", texto)
    if m:
        return m.group(1).strip()

    return None


def _resolver_referencia_anaforica(mem: dict, sesion_id: str = "") -> dict | None:
    """
    Busca en el último turno de Aether (en esta sesión) el "payload" relevante
    que la orden actual ("ejecutalo", "guardalo", "mandaselo", etc.) esté
    refiriendo.

    Generalización más allá de comandos shell:
    - Si el turno previo tenía un comando (tema shell/codigo), lo extrae para
      pasarlo explícitamente a node_shell (evita que LLM regenere y alucine).
    - Si el turno previo era "web" / "vision" / etc, captura el texto de la
      respuesta para poder usarlo en acciones generalizadas (ej. guardalo via
      file_write prefill).

    Retorna dict con:
      {"command": "..." | None, "tema": "...", "last_texto": "...", ... }
    o None si no hay turno de Aether usable en la sesión.

    Si retorna dict pero sin "command", el caller decide cómo actuar
    (file_write, text, o pedir aclaración).
    """
    turnos = mem.get("conversacion") or []
    sesion_actual = sesion_id or _sesion_actual_mem(mem)

    turnos_sesion = (
        [t for t in turnos if t.get("sesion_id") == sesion_actual]
        if sesion_actual else turnos
    )

    # Último turno de Aether (rol jarvis/asistente/aether), buscando hacia atrás.
    for t in reversed(turnos_sesion):
        rol = str(t.get("rol", "")).lower()
        if rol in ("jarvis", "assistant", "asistente", "aether"):
            texto = str(t.get("texto", ""))
            comando = _extraer_comando_de_texto(texto)
            tema = str(t.get("tema", "")).strip().lower() or "desconocido"
            if comando:
                return {
                    "command": comando,
                    "tema": tema,
                    "last_texto": texto,
                }
            # Sin comando extraíble, pero hay un turno previo de Aether con contenido.
            # Útil para generalizar a "guardalo" (web/vision data), "mandaselo", etc.
            if texto.strip():
                return {
                    "command": None,
                    "tema": tema,
                    "last_texto": texto,
                }
            break  # el turno más reciente de Aether no tenía payload usable

    return None


def _sesion_actual_mem(mem: dict) -> str:
    """Espejo local de context_builder._sesion_actual, para no crear import circular."""
    turnos = mem.get("conversacion") or []
    if turnos:
        return turnos[-1].get("sesion_id", "")
    return ""


def _plan_aclaracion(pregunta: str, orden: str, mem: dict | None = None) -> dict:
    """
    Construye la actualización de estado para pedir aclaración al usuario
    en vez de ejecutar un plan a ciegas. Termina el grafo en este turno;
    la respuesta de Aether es la pregunta, sin tocar shell/web/launch/etc.
    """
    update = {
        "plan_activo":     False,
        "plan_pasos":      [],
        "plan_index":      0,
        "plan_resultados": [],
        "final_response":  pregunta,
        "done":            True,
        "messages":        [HumanMessage(content=orden), AIMessage(content=pregunta)],
    }
    if mem is not None:
        update["mem"] = mem
    return update


def _plan_activado(plan: list[dict], orden: str, mem: dict | None = None) -> dict:
    """Construye la actualización de estado que activa un plan."""
    update = {
        "plan_activo":     True,
        "plan_pasos":      plan,
        "plan_index":      0,
        "plan_resultados": [],
        "error_activo":    False,
        "error_intento":   0,
        "messages":        [HumanMessage(content=orden)],
    }
    if mem is not None:
        update["mem"] = mem
    return update


def _decidir_intencion_con_razonamiento(orden: str, mem: dict) -> str:
    from core.tools.mcp_client import get_mcp_manager
    from core.agent.graph_nodes import obtener_catalogo_mcp_condensado

    manager = get_mcp_manager()
    mcp_catalog = obtener_catalogo_mcp_condensado(manager)
    mcp_info = f"\n\nMCP disponibles:\n{mcp_catalog}" if mcp_catalog and "No hay servidores" not in mcp_catalog else ""
    
    """
    Nueva filosofía: la detección de intención SIEMPRE pasa por el
    razonamiento del modelo (Ornith).
    """
    from core.agent.tool_registry import TOOLS_VALIDAS
    
    # DETECCIÓN PRIORITARIA: Si el usuario menciona MCP explícitamente
    # Y ADEMÁS hay un verbo de acción -> es una orden real, no solo un
    # comentario/mención casual (ej: "che, la integración de Model Context
    # Protocol quedó buenísima" NO debe forzar intent=mcp).
    orden_lower = orden.lower()
    orden_norm = _normalizar(orden_lower)
    _VERBOS_ACCION_MCP = (
        "busca", "buscá", "buscame", "buscar", "lista", "listame", "listar",
        "mostrame", "mostrar", "muestra", "trae", "traeme", "traer",
        "ejecuta", "ejecutá", "ejecutar", "usa", "usá", "usar",
        "consulta", "consultá", "consultar", "revisa", "revisá", "revisar",
        "dame", "abrí", "abre", "corre", "corré", "invoca", "invocá",
    )
    tiene_verbo_accion = any(v in orden_norm for v in _VERBOS_ACCION_MCP)
    if tiene_verbo_accion and any(kw in orden_norm for kw in _KW_MCP_N):
        print("   └─ [PLANNER]: MCP solicitado explícitamente por el usuario")
        return "mcp"
    
    system = (
        "Eres un analizador de intenciones muy preciso. Tu trabajo es "
        "razonar sobre qué necesita realmente el usuario y decidir la "
        "herramienta más adecuada (o si es solo charla). "
        "Prioriza 'launch' cuando el usuario quiere abrir, ejecutar, lanzar o jugar una aplicación, juego o programa (incluyendo Flatpaks como Sober, Roblox, Firefox, etc.). "
        "Usa 'shell' solo para comandos de terminal, archivos, procesos, etc. "
        "Usa 'launch' para apps gráficas y programas instalados. "
        "Usa 'mcp' cuando el usuario pida explícitamente usar MCP o Model Context Protocol."
    )
    user = f"""Orden del usuario: "{orden}"
Herramientas disponibles:
web: buscar información actual (precios, noticias, versiones, clima)
shell: comandos de sistema (ls, pip cache, df, procesos, archivos)
launch: abrir/ejecutar/lanzar programas, aplicaciones, juegos (Flatpak o PATH). Ej: "ejecuta sober", "abre firefox", "quiero jugar roblox", "lanza el programa"
vision: capturar y analizar la pantalla
codigo: escribir y ejecutar código nuevo
memory: recordar o gestionar datos del usuario
mcp: invocar herramientas externas vía Model Context Protocol (cuando el usuario lo pida explícitamente)
text: charla normal, conversación, conocimiento general
Piensa paso a paso (razonamiento detallado) sobre la orden.
Al final de tu razonamiento, responde exactamente con una línea:
TOOL: launch
TOOL: shell
TOOL: web
TOOL: text
TOOL: vision
TOOL: codigo
TOOL: memory
TOOL: mcp
Ejemplos:
"ejecuta sober" o "quiero jugar" → launch (es un programa Flatpak)
"lista archivos" → shell
"busca versión de python" → web
"hola" → text
"abre firefox" → launch
"listame directorios usando MCP" → mcp
Tu razonamiento:"""
    try:
         raw = _llm_chat(system=system, user=user).strip()
    except Exception as e:
        print(f"   └─ [PLANNER]: razonamiento de intención falló ({e}); usando 'text'.")
        return "text"
    # Buscamos la línea TOOL: xxx
    match = re.search(r"TOOL:\s*([a-z]+)", raw, re.IGNORECASE)
    if match:
        tool = match.group(1).lower()
        if tool in TOOLS_VALIDAS or tool == "mcp":
            return tool
    # Fallback: última palabra válida
    for palabra in re.findall(r"[a-z]+", raw.lower())[::-1]:
        if palabra in TOOLS_VALIDAS or palabra == "mcp":
            return palabra
    return "text"


def node_planner(state: AetherState) -> dict:
    """
    ÚNICA puerta de decisión del grafo.

    Filosofía actual: toda detección de intención pasa por el
    razonamiento explícito del modelo (Ornith). No dependemos
    principalmente de keywords frágiles.
    """
    from core.agent.tool_registry import validar_plan

    orden = state["orden"]
    orden_lower = orden.lower()
    mem = normalizar_mem(state.get("mem"))

    print("\n🧠 [PLANNER]: Decidiendo plan de ejecución con razonamiento del modelo...")

    # ═══════════════════════════════════════════════════════════════════
    # CASO 0: RESOLUCIÓN DE AMBIGÜEDAD ANAFÓRICA
    # ══════════════════════════════════════════════════════════════════
    if _posible_referencia_anaforica(orden_lower):
        sesion_id = state.get("sesion_id", "")
        es_anaforica = _confirmar_referencia_anaforica_llm(orden)
        if es_anaforica:
            ref = _resolver_referencia_anaforica(mem, sesion_id=sesion_id)
            if ref:
                cmd = ref.get("command")
                if cmd:
                    cmd_clean = cmd.strip()
                    is_likely_app = bool(re.match(r'^[a-zA-Z0-9\.-]+$', cmd_clean)) and len(cmd_clean.split()) <= 1
                    tema_prev = ref.get("tema", "")
                    last_lower = (ref.get("last_texto") or "").lower()

                    if is_likely_app or tema_prev == "launch" or any(w in last_lower for w in ("lanza", "abre", "ejecuta", "flatpak", "jugar", "programa")):
                        print(f"   └─ Referencia anafórica resuelta → launch: {cmd}")
                        launch_instruccion = orden
                        if cmd.strip().startswith("flatpak run "):
                            parts = cmd.strip().split()
                            if len(parts) >= 3:
                                app_id = parts[2]
                                launch_instruccion = f"ejecuta {app_id}"
                        return _plan_activado(
                            [{"tool": "launch", "instruccion": launch_instruccion, "args": {}}],
                            orden, mem,
                        )
                    else:
                        print(f"   └─ Referencia anafórica resuelta → shell: {cmd}")
                        return _plan_activado(
                            [{"tool": "shell", "instruccion": orden, "args": {"command": cmd}}],
                            orden, mem,
                        )

                tema_prev = ref.get("tema", "")
                last_texto = ref.get("last_texto") or ""
                o_norm = _normalizar(orden_lower)

                _EXEC_REFS = ("ejecuta", "ejecutalo", "dale", "hacelo", "hazlo",
                              "corre", "anda", "procede", "adelante", "confirmado")
                parece_ejecutar = any(
                    re.search(rf"\b{re.escape(k)}\b", o_norm)
                    for k in _EXEC_REFS
                )
                if parece_ejecutar:
                    print("   └─ Referencia anafórica de ejecución SIN comando en antecedente → aclaración.")
                    return _plan_aclaracion(
                        "Decime con qué exactamente — no tengo un comando o acción concreta "
                        "de la que veníamos hablando para ejecutar ahora.",
                        orden, mem,
                    )

                parece_guardar = any(k in o_norm for k in ("guarda", "guardalo", "guardar", "archivo", "escribi"))
                if parece_guardar and last_texto.strip():
                    fname = "resultado_anterior.txt"
                    m = re.search(r'[\w\-.]+\.(?:txt|md|json|csv|log|out)', orden)
                    if m:
                        fname = m.group(0)
                    plan_paso = {
                        "tool": "file_write",
                        "instruccion": orden,
                        "args": {"filename": fname},
                    }
                    upd = _plan_activado([plan_paso], orden, mem)
                    upd["plan_resultados"] = [last_texto[:2000]]
                    print(f"   └─ Referencia anafórica generalizada → file_write (tema_prev={tema_prev}, prefill)")
                    return upd

                print(f"   ─ Referencia anafórica generalizada (tema_prev={tema_prev}) → text")
                return _plan_activado(
                    [{"tool": "text", "instruccion": orden}],
                    orden, mem,
                )

            print("   └─ Referencia anafórica SIN antecedente claro → pidiendo aclaración.")
            return _plan_aclaracion(
                "Decime con qué exactamente — no tengo un comando o acción concreta "
                "de la que veníamos hablando para ejecutar ahora.",
                orden, mem,
            )

        print("   └─ Posible anafórica pero LLM determinó que es autosuficiente. Siguiendo flujo normal.")

    # ═══════════════════════════════════════════════════════════════════
    # DETECCIÓN DE INTENCIÓN
    # ══════════════════════════════════════════════════════════════════
    intent = _decidir_intencion_con_razonamiento(orden, mem)
    multi = _parece_multitool(orden_lower)

    # CASO ESPECIAL: MCP con inferencia de args
    if intent == "mcp" and not multi:
        print(f"   └─ Intención decidida por razonamiento del modelo: {intent}")
        from core.tools.mcp_client import get_mcp_manager
        manager = get_mcp_manager()
        args_mcp = _inferir_args_mcp(orden, manager)
        print(f"   └─ Args MCP inferidos: {args_mcp}")
        return _plan_activado(
            [{"tool": "mcp", "instruccion": orden, "args": args_mcp}],
            orden, mem
        )

    # CASO GENERAL: single-tool
    if intent and not multi:
        print(f"   └─ Intención decidida por razonamiento del modelo: {intent}")
        return _plan_activado([{"tool": intent, "instruccion": orden}], orden, mem)

    # ═══════════════════════════════════════════════════════════════════
    # CASO 1.5: PERSISTENCIA + MULTI-TOOL
    # ═══════════════════════════════════════════════════════════════════
    _KW_PERSISTENCIA_N = frozenset(_normalizar(p) for p in _KW_PERSISTENCIA)
    o_norm = _normalizar(orden_lower)
    tiene_persistencia = any(p in o_norm for p in _KW_PERSISTENCIA_N)

    if multi and tiene_persistencia and intent in ("web", "vision", "codigo"):
        import re as _re
        m_fname = _re.search(r'[\w\-]+\.(?:txt|md|json|csv|py|sh|html)', orden)
        filename = m_fname.group(0) if m_fname else f"{intent}_resultado.txt"

        _TOOLS_CONTENIDO_CRUDO = frozenset({"web", "vision"})

        plan_det = [{"tool": intent, "instruccion": orden, "args": {}}]
        if intent in _TOOLS_CONTENIDO_CRUDO:
            plan_det.append({"tool": "extract", "instruccion": orden, "args": {}})
        plan_det.append({"tool": "file_write", "instruccion": f"guardar resultado en {filename}", "args": {"filename": filename}})

        pasos_desc = " → ".join(p["tool"] for p in plan_det)
        print(f"   └─ Plan determinista (razonamiento + persistencia): {pasos_desc}")
        return _plan_activado(plan_det, orden, mem)

    # ═══════════════════════════════════════════════════════════════════
    # CASO MULTI-TOOL: planner LLM
    # ═══════════════════════════════════════════════════════════════════
    if multi:
        print("   └─ Posible multi-tool según heurística, consultando LLM para el plan...")
        try:
            plan_llm = _planner_llm(orden, mem)
            if plan_llm:
                ok, errores = validar_plan(plan_llm)
                if ok:
                    print(f"   └─ Plan multi-tool válido: {len(plan_llm)} pasos")
                    return _plan_activado(plan_llm, orden, mem)
                print(f"   └─ Plan del LLM inválido: {errores}. Usando razonamiento simple.")
        except Exception as e:
            print(f"   └─ Error en plan multi-tool ({e}).")

    # ═══════════════════════════════════════════════════════════════════
    # FALLBACK
    # ═══════════════════════════════════════════════════════════════════
    print("   └─ Decisión final por razonamiento del modelo...")
    tool_fallback = _decidir_intencion_con_razonamiento(orden, mem)
    print(f"   └─ Plan de 1 paso (razonamiento): tool={tool_fallback}")
    return _plan_activado([{"tool": tool_fallback, "instruccion": orden}], orden, mem)

# ══════════════════════════════════════════════════════════════════════
# NODO: ROUTER (DEPRECADO — conservado por compatibilidad)
# ══════════════════════════════════════════════════════════════════════

def node_router(state: AetherState) -> dict:
    """[DEPRECADO] Ya NO está cableado en el grafo. Usa node_planner."""
    orden = state["orden"].lower()
    intent = _detectar_intent_keywords(orden)

    if intent is None:
        clasificacion = _llm_chat(
            system="Eres un clasificador de intenciones. Responde SOLO con una palabra en minúsculas.",
            user=(
                f"Clasifica esta orden del usuario en UNA de estas categorías:\n"
                f"- web, shell, launch, vision, codigo, memory, text\n\n"
                f"Orden: {state['orden']}\n\n"
                f"Categoría:"
            )
        ).strip().lower()
        intent = clasificacion if clasificacion in ["web", "shell", "launch", "vision", "codigo", "memory", "text"] else "text"

    return {
        "intent":        intent,
        "error_activo":  False,
        "error_intento": state.get("error_intento", 0),
        "messages":      [HumanMessage(content=state["orden"])],
        "plan_activo":   False,
        "plan_index":    0,
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: WEB — proveedor de datos puro
# ══════════════════════════════════════════════════════════════════════

def node_web(state: AetherState) -> dict:
    """
    Búsqueda web + lectura de URL.
    REFACTOR: NO sintetiza con LLM. Devuelve datos crudos en web_results
    para que Ornith (plan_synthesizer) los procese.
    """
    orden = state["orden"]

    print("\n🔍 [WEB]: Buscando...")
    resultados = buscar_web.invoke(orden)

    urls = re.findall(r"URL:\s*(https?://\S+)", resultados)
    contenido_url = ""
    if urls:
        print(f"📖 [WEB]: Leyendo {urls[0]}...")
        contenido_url = leer_url.invoke(urls[0])

    contexto_web = resultados
    if contenido_url:
        contexto_web += f"\n\n[CONTENIDO LEÍDO]\n{contenido_url[:3000]}"

    print(f"   └─ [WEB]: {len(contexto_web)} caracteres de datos obtenidos.")

    return {
        "web_results":    contexto_web,
        "llm_response":   None,
        "final_response": None,
        "messages":       [HumanMessage(content=orden)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: MCP — invoca una tool de un servidor MCP externo conectado
# ══════════════════════════════════════════════════════════════════════

def _args_del_paso_mcp(state: AetherState) -> dict:
    """
    Lee los args del paso MCP actual desde plan_pasos[plan_index].
    Versión robusta: valida que args sea SIEMPRE un dict.
    """
    plan_pasos = state.get("plan_pasos") or []
    idx = state.get("plan_index", 0)
    
    if not isinstance(idx, int) or idx < 0:
        idx = 0
    
    if idx < len(plan_pasos):
        paso = plan_pasos[idx]
        if isinstance(paso, dict):
            args = paso.get("args")
            # VALIDACIÓN ESTRICTA: si args no es dict, usar {}
            if isinstance(args, dict):
                return args
            elif args is not None:
                print(f"   └─ ⚠️  [MCP]: args es {type(args).__name__} en lugar de dict. Usando {{}}.")
                return {}
    
    return {}

def node_mcp(state: AetherState) -> dict:
    """
    Invoca una tool de un servidor MCP externo, vía MCPClientManager
    (conexión persistente, ver core/tools/mcp_client.py).

    Espera args={"server": "...", "name": "...", "arguments": {...}}
    en el paso actual del plan. NO sintetiza con LLM — devuelve el dato
    crudo en mcp_result, igual que node_web/node_shell, para que
    plan_synthesizer razone sobre él.
    """
    from core.tools.mcp_client import get_mcp_manager, MCPError

    args = _args_del_paso_mcp(state)
    
    # VALIDACIÓN AGREGADA: por si _args_del_paso_mcp falla
    if not isinstance(args, dict):
        msg = f"args debe ser dict, no {type(args).__name__}"
        print(f"   └─ ❌ [MCP]: {msg}")
        return {
            "error_activo":   True,
            "error_mensaje":  msg,
            "error_contexto": "mcp",
        }
    
    server = args.get("server")
    name = args.get("name")
    arguments = args.get("arguments") or {}

    if not server or not name:
        # IMPORTANTE: esto NO es un error de ejecución MCP (no se llegó a
        # invocar ningún server), es un fallo de PLANIFICACIÓN — el usuario
        # mencionó MCP pero no hay una tool concreta que inferir de la orden
        # (ej: comentario conversacional que solo contiene la palabra "MCP").
        # No debe pasar por el pipeline de auto-diagnóstico (que buscaría en
        # la web y propondría ejecutar comandos shell para "arreglar" algo
        # que no está roto). En cambio, respondemos conversacionalmente.
        orden_usuario = state.get("orden", "")
        msg = (
            "Mencionaste MCP pero no identifiqué una acción concreta para ejecutar "
            f"a partir de: \"{orden_usuario}\". ¿Qué querés que haga puntualmente "
            "(ej: buscar algo en GitHub, listar archivos, consultar Notion)?"
        )
        print(f"   └─ ⚠️  [MCP]: no se pudo inferir server/tool desde la orden; respondiendo sin diagnosticar.")
        return {
            "mcp_result":     "",
            "llm_response":   None,
            "final_response": msg,
            "error_activo":   False,
            "messages":       [HumanMessage(content=orden_usuario)],
        }

    print(f"\n🔌 [MCP]: Llamando '{name}' en server '{server}'...")

    try:
        manager = get_mcp_manager()
        resultado = manager.call_tool(server, name, arguments)
    except MCPError as e:
        print(f"   └─ ❌ [MCP]: {e}")
        return {
            "error_activo":   True,
            "error_mensaje":  str(e),
            "error_contexto": "mcp",
        }
    except Exception as e:
        print(f"   └─ ❌ [MCP]: error inesperado: {e}")
        return {
            "error_activo":   True,
            "error_mensaje":  f"Error inesperado llamando MCP '{name}' en '{server}': {e}",
            "error_contexto": "mcp",
        }

    print(f"   └─ [MCP]: {len(resultado)} caracteres de datos obtenidos.")

    return {
        "mcp_result":     resultado,
        "llm_response":   None,
        "final_response": None,
        "messages":       [HumanMessage(content=state.get("orden", ""))],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: SHELL — proveedor de datos puro
# ══════════════════════════════════════════════════════════════════════

def _comando_explicito_del_paso(state: AetherState) -> str | None:
    """
    Si el paso actual del plan trae args={"command": "..."} (típicamente
    seteado por la resolución de referencias anafóricas en node_planner),
    lo devuelve para que node_shell lo use directo sin pasar por el LLM.

    NOTA: el state que recibe node_shell es sub_estado (ver node_plan_executor),
    donde plan_index AÚN apunta al paso que se está ejecutando ahora mismo
    (el incremento a plan_index+1 ocurre recién después, en plan_executor).
    """
    plan_pasos = state.get("plan_pasos") or []
    idx = state.get("plan_index", 0)
    if not isinstance(idx, int) or idx < 0:
        idx = 0
    if idx < len(plan_pasos) and isinstance(plan_pasos[idx], dict):
        args = plan_pasos[idx].get("args") or {}
        cmd = args.get("command")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()
    return None


def node_shell(state: AetherState) -> dict:
    """
    LLM genera un comando shell → se extrae → se ejecuta.
    REFACTOR: La respuesta LLM interna es solo para generar el comando.
    Los datos crudos (shell_output) quedan en estado para que Ornith los procese.
    Si hay error → activa error handler.

    Si el planner ya resolvió el comando exacto (ej. referencia anafórica
    "ejecutalo" resuelta contra el turno anterior), se usa ese comando
    directamente — el LLM no debe regenerarlo desde cero ni arriesgarse
    a alucinar uno distinto al que el usuario confirmó.
    """
    orden     = state["orden"]
    mem       = state["mem"]
    modo_auto = state.get("modo_autonomo", True)

    comando_explicito = _comando_explicito_del_paso(state)

    if comando_explicito:
        print(f"   └─ [SHELL]: Comando ya resuelto por el planner, sin pasar por LLM.")
        comando  = comando_explicito
        llm_resp = f"[SHELL]{comando_explicito}[/SHELL]"
    else:
        # LLM interno: solo para generar el comando, no para responder al usuario
        # Always strip Ornith <think> before extracting the [SHELL] command.
        raw_resp = _llm_chat(
            system=_system_prompt(mem, state),
            user=orden,
        )
        _, llm_resp = _parse_ornith_thinking(raw_resp)

        comando = extraer_comando_shell(llm_resp)

        if not comando:
            print("⚠️  [SHELL]: No se detectó formato [SHELL]...[/SHELL], reintentando...")
            raw_retry = _llm_chat(
                system=_system_prompt(mem, state),
                user=(
                    f"Genera SOLO el comando shell para ejecutar esta orden.\n"
                    f"Orden: {orden}\n\n"
                    f"IMPORTANTE: Devuelve el comando en formato [SHELL]comando[/SHELL]\n"
                    f"Sin explicaciones adicionales."
                ),
            )
            _, llm_resp_retry = _parse_ornith_thinking(raw_retry)
            comando = extraer_comando_shell(llm_resp_retry)
            if comando:
                print(f"   └─ Comando extraído tras reintento: {comando}")

    if not comando:
        # Sin comando válido → respuesta de texto plano sin síntesis
        return {
            "llm_response":   llm_resp,
            "shell_command":  None,
            "final_response": llm_resp,
            "messages":       [AIMessage(content=llm_resp)],
        }

    print(f"\n⚠️  [SHELL DETECTADO]: \033[1;33m{comando}\033[0m")
    if not modo_auto:
        if not _confirmar_usuario("¿Ejecutar este comando?"):
            return {
                "llm_response":   llm_resp,
                "shell_command":  comando,
                "shell_output":   "",
                "shell_error":    False,
                "final_response": "Ejecución cancelada.",
                "done":           True,
            }

    print("\n⚙️  [EJECUTANDO EN ZSH...]")
    salida, hubo_error = ejecutar_comando(comando)
    print(f"\n{'─'*50}\n{salida}\n{'─'*50}")

    registrar_comando(mem, orden, comando)

    if hubo_error:
        print(f"\n❌ [ERROR SHELL]: Activando diagnóstico automático...")
        return {
            "llm_response":   llm_resp,
            "shell_command":  comando,
            "shell_output":   salida,
            "shell_error":    True,
            "error_activo":   True,
            "error_mensaje":  salida,
            "error_contexto": "shell",
            "messages":       [AIMessage(content=llm_resp)],
        }

    # Datos crudos en estado; Ornith los sintetiza en plan_synthesizer
    return {
        "llm_response":   llm_resp,
        "shell_command":  comando,
        "shell_output":   salida,
        "shell_error":    False,
        "error_activo":   False,
        "final_response": None,   # ← Ornith sintetiza
        "messages":       [AIMessage(content=llm_resp)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: LAUNCH (programas gráficos y nativos)
# ══════════════════════════════════════════════════════════════════════

def _terminos_de_orden(orden: str) -> list[str]:
    """Extrae palabras clave de la orden, filtrando stop words y KW_LAUNCH.
    Remueve palabras meta relacionadas con flatpak y lanzamiento.
    """
    LAUNCH_META = {"flatpak", "mediante", "instalado", "por", "quiero", "necesito", "esta", "el", "la", "que", "necesitaria"}
    stop = _STOP_WORDS | _KW_LAUNCH_N | LAUNCH_META
    return [w for w in orden.lower().split() if w not in stop and len(w) > 2]


def _extraer_nombre_programa(orden: str) -> str:
    """Extrae el nombre del programa que el usuario quiere lanzar.
    Soporta "ejecuta prism launcher", "abre prismlauncher", IDs de flatpak, etc.
    """
    if not orden:
        return ""

    o = " " + orden.lower() + " "

    # Quitar verbos y meta de lanzamiento
    for palabra in ("ejecuta", "abre", "lanza", "inicia", "corre", "arranca", "quiero ejecutar",
                    "quiero abrir", "necesito", "por favor", "dale", "haz", "haceme"):
        o = o.replace(palabra, " ")

    # Meta de flatpak
    for palabra in ("flatpak", "mediante flatpak", "por flatpak", "esta instalado mediante flatpak",
                    "instalado con flatpak", "mediante", "instalado"):
        o = o.replace(palabra, " ")

    # Normalizaciones conocidas
    o = re.sub(r"\bprism\s*launcher\b", "prismlauncher", o)
    o = re.sub(r"\bprism-launcher\b", "prismlauncher", o)

    # Quitar artículos y ruido
    o = re.sub(r"\b(el|la|los|las|un|una|este|esta|el\s+programa|la\s+app)\b", " ", o)

    # Buscar algo que parezca ID completo de flatpak (org.foo.Bar)
    m = re.search(r"\b([a-z0-9_][a-z0-9_.-]*\.[a-z0-9_.-]+\.[a-z0-9_.-]+)\b", o)
    if m:
        return m.group(1)

    # Extraer tokens tipo programa
    tokens = re.findall(r"[a-z0-9][a-z0-9._-]{2,}", o)
    # Filtrar genéricos
    genericos = {"programa", "app", "aplicacion", "juego", "lanzador", "launcher", "binario", "ejecutable"}
    tokens = [t for t in tokens if t not in genericos and len(t) > 2]

    if not tokens:
        # último recurso: última palabra significativa
        palabras = [w.strip(".,;:") for w in orden.lower().split() if len(w) > 2]
        return palabras[-1] if palabras else orden.strip().lower()

    # Preferir el token más largo o el último relevante
    tokens.sort(key=len, reverse=True)
    return tokens[0]


def _buscar_flatpak_por_nombre(nombre: str) -> str | None:
    """Busca el programa en la lista fresca de flatpaks (siempre lee la lista actual).
    Coincide contra el último segmento del ID y contra el nombre humano.
    """
    if not nombre:
        return None

    nombre_limpio = nombre.lower().strip()
    # normalizaciones
    if "prism" in nombre_limpio and "launch" in nombre_limpio:
        nombre_limpio = "prismlauncher"

    salida, err = ejecutar_comando(
        "flatpak list --app --columns=application,name 2>/dev/null"
    )
    if err or not salida:
        return None

    lines = [l.strip() for l in salida.strip().splitlines() if l.strip()]

    best_id = None
    best_score = 0.0

    GENERIC = {"launcher", "app", "client", "game", "desktop"}

    for line in lines:
        parts = line.split(None, 1)
        if len(parts) < 1:
            continue
        app_id = parts[0].strip()
        human_name = parts[1].strip() if len(parts) > 1 else ""

        last_seg = app_id.split(".")[-1].lower()
        candidates = [last_seg, app_id.lower(), human_name.lower()]

        # 1. Coincidencia directa fuerte (nombre o segmento)
        for cand in candidates:
            if nombre_limpio and (nombre_limpio in cand or cand in nombre_limpio):
                return app_id

        # 2. Fuzzy con guardia contra sufijos genéricos
        for cand in candidates:
            r = difflib.SequenceMatcher(None, nombre_limpio, cand).ratio()

            # Penalizar si el nombre pedido tiene parte distintiva que no está en el candidato
            core = re.sub(r"(launcher|app|client|game|studio)$", "", nombre_limpio)
            if len(core) >= 4 and core not in cand and not any(core[:4] in c for c in candidates):
                r *= 0.3

            if r > best_score:
                best_score = r
                best_id = app_id

    if best_id and best_score > 0.58:
        return best_id
    return None


def _resolver_con_which(nombre: str) -> str | None:
    """Ejecuta explícitamente 'which {programa}' (o fallback a command -v) como pidió el usuario.
    Retorna el binario/path encontrado o None.
    """
    if not nombre:
        return None
    prog = nombre.strip().strip(",.;:")
    cmd = f'which {shlex.quote(prog)} 2>/dev/null || command -v {shlex.quote(prog)} 2>/dev/null'
    salida, _ = ejecutar_comando(cmd)
    if salida and salida.strip():
        # tomar la primera línea útil
        for linea in salida.strip().splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("which:"):
                # Defensa contra mensajes falsos del launcher GUI (ej "[Proceso lanzado...")
                if "[" in linea or "Proceso" in linea or "lanzado" in linea.lower():
                    continue
                return linea
    return None


def _buscar_flatpak_rapido(terminos: list[str]) -> str | None:
    """Compatibilidad. Usa la lógica nueva basada en nombre."""
    if not terminos:
        return None
    # Tomamos el token más probable como nombre de programa
    for t in reversed(terminos):
        if len(t) > 2 and t not in {"flatpak", "mediante"}:
            res = _buscar_flatpak_por_nombre(t)
            if res:
                return res
    # último intento con el primero
    return _buscar_flatpak_por_nombre(terminos[0]) if terminos else None


def _buscar_en_path(terminos: list[str]) -> str | None:
    """Busca ejecutables en PATH y sistema de forma más amplia (flatpak o nativo).
    Ignora términos meta como 'flatpak'.
    También busca en .desktop files y variantes comunes de nombres (prismlauncher etc).
    """
    meta_terms = {"flatpak", "mediante", "instalado", "por"}

    def _candidates(t: str) -> list[str]:
        t = t.lower()
        c = [t]
        if "prism" in t:
            for v in ("prismlauncher", "prism-launcher", "prism_launcher", "prism"):
                if v not in c:
                    c.append(v)
        if t.endswith("launcher"):
            base = t[:-8]
            for v in (base, base + "-launcher", base + "_launcher"):
                if v and v not in c:
                    c.append(v)
        if "launcher" in t and len(t) > 8:
            # also try without launcher suffix as direct bin
            no_launcher = t.replace("launcher", "").strip("-_")
            if no_launcher and no_launcher not in c and len(no_launcher) > 2:
                c.append(no_launcher)
        # dedup preserve order
        seen = set()
        out = []
        for x in c:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    for termino in reversed(terminos):
        if termino in meta_terms:
            continue
        for cand in _candidates(termino):
            termino_seguro = shlex.quote(cand)
            # Try direct in PATH (fast)
            salida, err = ejecutar_comando(f"command -v {termino_seguro} 2>/dev/null || command -v {shlex.quote(termino)} 2>/dev/null")
            if not err and salida.strip():
                found = salida.strip().splitlines()[0].strip()
                return _os.path.basename(found)

            # Broader system search (native executables)
            dirs = "/usr/bin /usr/local/bin /usr/games /opt /snap/bin /var/lib/flatpak/exports/bin ~/.local/bin ~/.local/share/flatpak/exports/bin"
            salida, err = ejecutar_comando(
                f"find {dirs} -type f -executable 2>/dev/null 2>&1 | grep -i {termino_seguro} | head -3"
            )
            if not err and salida.strip():
                first = salida.strip().splitlines()[0].strip()
                found = _os.path.basename(first)
                if found:
                    return found

            # .desktop files (Name= or Exec=)
            salida, err = ejecutar_comando(
                f"find /usr/share/applications ~/.local/share/applications -name '*.desktop' 2>/dev/null "
                f"| xargs grep -l -i {termino_seguro} 2>/dev/null | head -1 "
                f"| xargs grep -E -m1 '^(Exec=|Name=)' 2>/dev/null | head -1 | cut -d= -f2 | cut -d' ' -f1"
            )
            if not err and salida.strip():
                found = _os.path.basename(salida.strip().split()[0])
                if found and len(found) > 1:
                    return found
    return None


def _launch_error(cmd: str, salida: str, orden: str) -> dict:
    print(f"\n❌ [LAUNCH ERROR]: {salida[:200]}")
    return {
        "shell_command":  cmd,
        "shell_output":   salida,
        "shell_error":    True,
        "error_activo":   True,
        "error_mensaje":  salida,
        "error_contexto": "launch",
    }


def node_launch(state: AetherState) -> dict:
    """
    Lanza programas siguiendo el flujo pedido:
      1. El usuario pide lanzar un programa.
      2. Se dispara búsqueda en flatpak (lista fresca).
      3. Si NO lo encuentra en flatpak → se ejecuta: which {programa} (o command -v).
      4. Se lanza lo que corresponda con PID limpio.
    """
    orden = state["orden"]
    mem   = state["mem"]

    mem.setdefault("flatpaks", {})
    mem.setdefault("executables", {})

    # Nombre limpio del programa objetivo
    prog = _extraer_nombre_programa(orden)
    if not prog:
        prog = orden.lower().strip()

    # Soporte básico para anáforas puras ("dale", "ejecutalo") que llegaron hasta acá
    if prog in ("dale", "ejecutalo", "hacelo", "lanzalo", "ejecutal o"):
        if mem.get("ultimo_lanzado"):
            prog = mem["ultimo_lanzado"]
            print(f"\n🔄 [LAUNCH]: Anáfora detectada → usando último programa: {prog}")
        else:
            prog = ""

    # Fast path por lanzamientos anteriores (guarda "firefox", "prismlauncher" o "flatpak run ID")
    prog_key = prog.lower().strip()
    if prog_key in mem["executables"]:
        cached = mem["executables"][prog_key]
        is_flatpak = cached.strip().startswith("flatpak run ")
        display = cached.replace("flatpak run ", "").strip() if is_flatpak else cached
        print(f"\n🚀 [LAUNCH]: (cached) {display}")
        launch_cmd = f"{cached} > /dev/null 2>&1 & echo $!"
        salida, hubo_error = ejecutar_comando(launch_cmd)
        pid = None
        if salida:
            for line in reversed([l.strip() for l in salida.strip().splitlines() if l.strip()]):
                if line.isdigit():
                    pid = line
                    break
        cmd = cached if cached.rstrip().endswith("&") else f"{cached} &"
        if pid:
            cmd = f"{cmd}  # PID {pid}"
        registrar_comando(mem, orden, cmd)
        mem["ultimo_lanzado"] = display
        if not hubo_error:
            msg = f"Lanzado {display} exitosamente (PID: {pid})" if pid else f"Lanzado {display}"
            return {"final_response": msg, "shell_command": cmd, "shell_error": False, "error_activo": False}
        return _launch_error(cmd, salida, orden)

    force_flatpak = "flatpak" in orden.lower()

    print(f"\n🔍 [LAUNCH]: Usuario quiere lanzar '{prog}'")

    # ============================================================
    # PASO 1: Buscar SIEMPRE primero en flatpak (lista fresca)
    # ============================================================
    print("\n🔍 [LAUNCH]: Buscando en flatpak (lista actual)...")
    try:
        list_out, _ = ejecutar_comando("flatpak list --app --columns=application,name 2>/dev/null")
        actualizar_flatpaks(mem, list_out)
    except Exception:
        pass

    app_id = _buscar_flatpak_por_nombre(prog)

    if app_id:
        print(f"\n🚀 [LAUNCH]: Encontrado en Flatpak → {app_id}")
        launch_cmd = f"flatpak run {app_id} > /dev/null 2>&1 & echo $!"
        salida, hubo_error = ejecutar_comando(launch_cmd)
        pid = None
        if salida:
            for line in reversed([l.strip() for l in salida.strip().splitlines() if l.strip()]):
                if line.isdigit():
                    pid = line
                    break
        cmd = f"flatpak run {app_id} &"
        if pid:
            cmd = f"{cmd}  # PID {pid}"
        registrar_comando(mem, orden, cmd)

        # Guardar en caché (por nombre original y por ID)
        mem["executables"][prog_key] = f"flatpak run {app_id}"
        last = app_id.split(".")[-1].lower()
        mem["executables"][last] = f"flatpak run {app_id}"
        mem["executables"][app_id.lower()] = f"flatpak run {app_id}"
        mem["ultimo_lanzado"] = prog

        if not hubo_error:
            msg = f"Lanzado {prog} (flatpak: {app_id}) exitosamente (PID: {pid})" if pid else f"Lanzado {prog} (flatpak: {app_id})"
            return {"final_response": msg, "shell_command": cmd, "shell_error": False, "error_activo": False}
        return _launch_error(cmd, salida, orden)

    # ============================================================
    # PASO 2: No estaba en flatpak → ejecutar which {programa}
    # ============================================================
    if force_flatpak:
        print(f"\n🔍 [LAUNCH]: Pediste flatpak pero '{prog}' no aparece en la lista. Usando which del sistema...")
    else:
        print(f"\n🔍 [LAUNCH]: No encontrado en flatpak. Ejecutando: which {prog}")
    resolved = _resolver_con_which(prog)

    if not resolved:
        # fallback ligero (por si which no está o el nombre es raro)
        print("\n🔍 [LAUNCH]: which no lo encontró, buscando más ampliamente...")
        resolved = _buscar_en_path([prog])

    # Limpiar y validar el resultado (defensa contra falsos positivos del detector GUI)
    binario = ""
    display_name = ""
    if resolved:
        clean = resolved.strip().splitlines()[0].strip() if resolved.strip() else ""
        candidate = clean.split()[0] if clean else ""
        if candidate and "[" not in candidate and "Proceso" not in candidate and "lanzado" not in candidate.lower():
            binario = candidate
            display_name = _os.path.basename(binario) if "/" in binario else binario

    if binario:
        print(f"\n🚀 [LAUNCH]: Binario del sistema → {binario}")

        launch_cmd = f"{binario} > /dev/null 2>&1 & echo $!"
        salida, hubo_error = ejecutar_comando(launch_cmd)
        pid = None
        if salida:
            for line in reversed([l.strip() for l in salida.strip().splitlines() if l.strip()]):
                if line.isdigit():
                    pid = line
                    break

        cmd = f"{binario} &"
        if pid:
            cmd = f"{cmd}  # PID {pid}"
        registrar_comando(mem, orden, cmd)

        # Cachear bajo el nombre que pidió el usuario y bajo el binario real
        mem["executables"][prog_key] = binario
        if display_name:
            mem["executables"][display_name.lower()] = binario
        mem["ultimo_lanzado"] = prog if prog else display_name

        if not hubo_error:
            msg = f"Lanzado {display_name} exitosamente (PID: {pid})" if pid else f"Lanzado {display_name}"
            return {"final_response": msg, "shell_command": cmd, "shell_error": False, "error_activo": False}
        return _launch_error(cmd, salida, orden)

    # ============================================================
    # Nada encontrado
    # ============================================================
    print("\n❌ [LAUNCH]: No se encontró el programa ni en flatpak ni en el sistema.")
    return {
        "shell_command":  None,
        "shell_output":   f"No se encontró '{prog}' en flatpak ni mediante which.",
        "shell_error":    True,
        "error_activo":   True,
        "error_mensaje":  f"No se encontró '{prog}' en flatpak ni mediante which.",
        "error_contexto": "launch",
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: CÓDIGO — proveedor de datos puro
# ══════════════════════════════════════════════════════════════════════

def _detectar_extension_codigo(texto: str) -> str:
    if "```python" in texto:                        return ".py"
    if "```java"   in texto:                        return ".java"
    if "```bash"   in texto or "```sh" in texto:    return ".sh"
    return ".py"


def _cmd_para_extension(ext: str, archivo: str) -> str | None:
    return {
        ".py": f"python3 {archivo}",
        ".sh": f"bash {archivo}",
        ".java": None,
    }.get(ext)


def node_codigo(state: AetherState) -> dict:
    """
    LLM genera código → se guarda en archivo temporal → se ejecuta si aplica.
    REFACTOR: final_response = None. Ornith sintetiza el resultado.
    Si la ejecución falla → activa error handler con contexto "codigo".
    """
    orden     = state["orden"]
    mem       = state["mem"]
    modo_auto = state.get("modo_autonomo", True)

    raw_resp = _llm_chat(
        system=_system_prompt(mem, state),
        user=orden,
    )
    _, llm_resp = _parse_ornith_thinking(raw_resp)

    bloques = re.findall(r"```(?:python|bash|sh|java)?\s*\n(.*?)\n```", llm_resp, re.DOTALL)
    if not bloques:
        return {
            "llm_response":   llm_resp,
            "final_response": llm_resp,
            "messages":       [AIMessage(content=llm_resp)],
        }

    codigo    = bloques[0].strip()
    extension = _detectar_extension_codigo(llm_resp)
    archivo_tmp = f"/tmp/aether_code{extension}"

    with open(archivo_tmp, "w", encoding="utf-8") as f:
        f.write(codigo)

    print(f"\n📄 [CÓDIGO]: Guardado en {archivo_tmp}")

    if not modo_auto:
        if not _confirmar_usuario(f"¿Ejecutar {archivo_tmp}?"):
            return {
                "llm_response":   llm_resp,
                "final_response": f"Código generado en {archivo_tmp} (no ejecutado).",
                "messages":       [AIMessage(content=llm_resp)],
            }

    cmd_ejecutar = _cmd_para_extension(extension, archivo_tmp)
    if not cmd_ejecutar:
        return {
            "llm_response":   llm_resp,
            "final_response": f"Código guardado en {archivo_tmp}. No sé cómo ejecutarlo automáticamente.",
            "messages":       [AIMessage(content=llm_resp)],
        }

    print(f"\n⚙️  [EJECUTANDO]: {cmd_ejecutar}")
    salida, hubo_error = ejecutar_comando(cmd_ejecutar)
    print(f"\n{'─'*50}\n{salida}\n{'─'*50}")

    if hubo_error:
        print(f"\n❌ [ERROR CÓDIGO]: Activando diagnóstico automático...")
        return {
            "llm_response":     llm_resp,
            "shell_command":    cmd_ejecutar,
            "shell_output":     salida,
            "shell_error":      True,
            "error_activo":     True,
            "error_mensaje":    salida,
            "error_contexto":   "codigo",
            "_codigo_original": codigo,
            "_archivo_codigo":  archivo_tmp,
            "messages":         [AIMessage(content=llm_resp)],
        }

    # Datos crudos en estado; Ornith sintetiza
    return {
        "llm_response":   llm_resp,
        "shell_command":  cmd_ejecutar,
        "shell_output":   salida,
        "shell_error":    False,
        "error_activo":   False,
        "final_response": None,   # ← Ornith sintetiza
        "messages":       [AIMessage(content=llm_resp)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: VISION — proveedor de datos puro
# ══════════════════════════════════════════════════════════════════════

def node_vision(state: AetherState) -> dict:
    """
    Captura pantalla → ver_pantalla().
    REFACTOR: NO pone final_response. Devuelve vision_result crudo
    para que Ornith (plan_synthesizer) lo procese y responda.
    """
    import time
    orden = state["orden"]

    print("\n👁️  [VISIÓN]: Activando en 5 segundos — mové el cursor al monitor deseado")
    for i in range(5, 0, -1):
        print(f"   ⏳ {i}...", end="\r", flush=True)
        time.sleep(1)
    print("   📸 Capturando...          ")

    pregunta = orden if len(orden) > 10 else (
        "Analiza esta imagen técnicamente. Lista todos los elementos visibles: "
        "texto, ventanas, programas abiertos y su contenido."
    )
    descripcion = ver_pantalla(pregunta)

    _ERRORES_VISION = (
        "No pude capturar pantalla",
        "No se generó screenshot",
        "El modelo de visión tardó demasiado",
        "Error interno en visión",
    )
    if any(descripcion.startswith(e) for e in _ERRORES_VISION):
        print(f"\n❌ [ERROR VISIÓN]: Activando diagnóstico automático...")
        return {
            "vision_result":  descripcion,
            "error_activo":   True,
            "error_mensaje":  descripcion,
            "error_contexto": "vision",
        }

    print(f"   └─ [VISIÓN]: descripción obtenida ({len(descripcion)} chars).")
    return {
        "vision_result":  descripcion,
        "final_response": None,   # ← Ornith sintetiza
        "messages":       [HumanMessage(content=orden)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: TEXT (conversación directa — sigue usando LLM, no es tool)
# ══════════════════════════════════════════════════════════════════════

def _system_prompt_chat(mem: dict, state: "AetherState | None" = None) -> str:
    """
    System prompt para node_text: charla pura, sin protocolo [SHELL].

    node_text es para conversación/conocimiento general — NUNCA debería
    ejecutar ni proponer comandos en formato [SHELL]. Usar _system_prompt()
    (con construir_backstory) acá fue la causa del bug "ejecutalo" →
    [SHELL] python -m pip cache purge [/SHELL] devuelto crudo al usuario:
    ese prompt enseña el protocolo de ejecución, pero node_text no tiene
    plomería para extraer y correr el comando.
    """
    from core.memory.context_builder import construir_contexto_memoria
    from core.agent.prompts import construir_persona_chat

    if state is not None:
        slots = state.get("context_slots") or {}
        contexto = slots.get("contexto", "")
        if contexto:
            return construir_persona_chat(contexto)

    contexto = construir_contexto_memoria(mem, tema="")
    return construir_persona_chat(contexto)


def node_text(state: AetherState) -> dict:
    """
    Respuesta directa via ollama streaming para texto/conversación.
    Este nodo SÍ llama al LLM porque no es una tool de datos:
    es el caso de charla/conocimiento general donde Ornith responde directo.
    """
    from core.agent.streaming import emit_token  # 👈 nuevo import

    orden = state["orden"]
    mem   = state["mem"]

    tokens: list[str] = []
    def _on_token(t):
        tokens.append(t)
        emit_token(t)   # 👈 antes: print del prefijo + print(t, end="", flush=True)

    try:
        raw = _llm_chat(
            system=_system_prompt_chat(mem, state),
            user=orden,
            on_token=_on_token,
        )
    except Exception as e:
        print(f"   └─ [TEXT]: Error llamando al modelo ({e}). Respuesta simple de fallback.")
        respuesta = "Disculpá, tuve un problema técnico ahora. ¿Podés repetir o reformular?"
        return {
            "llm_response": respuesta,
            "final_response": respuesta,
            "messages": [AIMessage(content=respuesta)],
        }

    # (se borra el `if tokens: print()` — ya no escribe a stdout)

    reasoning, respuesta = _parse_ornith_thinking(raw)
    if reasoning:
        print(f"\n🧠 [Ornith thinking (chat)]: {reasoning[:250]}{'...' if len(reasoning)>250 else ''}")

    comando_colado = _extraer_comando_de_texto(respuesta)
    if comando_colado:
        print(f"\n⚠️  [TEXT]: El LLM generó un comando pese al prompt de charla.")
        respuesta = f"Para eso necesito ejecutar `{comando_colado}` — ¿confirmás que lo corra?"

    respuesta = respuesta.replace("[SHELL]", "`").replace("[/SHELL]", "`")

    return {
        "llm_response":   respuesta,
        "final_response": respuesta,
        "messages":       [AIMessage(content=respuesta)],
        "_ornith_reasoning": reasoning,
    }

# ══════════════════════════════════════════════════════════════════════
# NODO: MEMORY
# ══════════════════════════════════════════════════════════════════════

def node_memory(state: AetherState) -> dict:
    """Procesa comandos especiales de memoria sin LLM."""
    import json
    orden = state["orden"]
    mem   = normalizar_mem(state.get("mem"))
    o     = _normalizar(orden).strip()
    resp  = ""

    _MOSTRAR = ("muestrame tu memoria", "muestrame mi memoria", "tu memoria",
                "mi memoria", "que recuerdas", "ver memoria", "mostrar memoria",
                "que sabes de mi", "que tienes guardado")
    if any(x in o for x in _MOSTRAR):
        resp = json.dumps(mem, ensure_ascii=False, indent=2)
        print(f"\n📋 [MEMORIA]:\n{resp}")

    elif any(x in o for x in ["borra la conversacion", "olvida la conversacion"]):
        mem["conversacion"] = []
        resp = "Historial borrado."

    elif any(x in o for x in ["borra los flatpaks", "olvida los flatpaks", "actualiza flatpaks"]):
        mem["flatpaks"] = {}
        resp = "Caché de Flatpaks reiniciado."

    else:
        m = re.search(r"mi nombre es ([A-Za-záéíóúÁÉÍÓÚñÑ]+)", orden, re.IGNORECASE)
        if m:
            nombre = m.group(1).strip().capitalize()
            mem["preferencias"]["nombre_usuario"] = nombre
            resp = f"Nombre registrado: {nombre}."

        m2 = re.search(r"tengo (\d+) años", orden, re.IGNORECASE)
        if m2:
            mem["preferencias"]["edad"] = int(m2.group(1))
            resp += f" Edad registrada: {m2.group(1)}."

        m3 = re.search(r"(?:recuerda|anota|guarda)\s+(?:que\s+)?(.+)", orden, re.IGNORECASE)
        if m3:
            nota = m3.group(1).strip()
            mem["preferencias"]["notas"].append(nota)
            mem["preferencias"]["notas"] = mem["preferencias"]["notas"][-10:]
            resp += f" Anotado: «{nota}»."

        from core.memory.memory_manager import guardar_memoria
        guardar_memoria(mem)

    print(f"\n🎙️  Aether: {resp}")
    return {
        "final_response": resp or "Operación de memoria completada.",
        "messages":       [AIMessage(content=resp)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: FINALIZE
# ══════════════════════════════════════════════════════════════════════

def node_finalize(state: AetherState) -> dict:
    """
    Registra SOLO la respuesta de Aether en DB y marca done=True.
    NO registra el turno del usuario (ya lo hace procesar_orden_completo).

    Si final_response es None (tool no sintetizó), intentar construir
    una respuesta de fallback desde los datos disponibles en el estado.
    """
    mem      = state["mem"]
    respuesta = state.get("final_response", "")

    # Fallback: si no hay final_response, usar el primer dato disponible
    if not respuesta:
        respuesta = (
            state.get("shell_output", "") or
            state.get("vision_result", "") or
            state.get("web_results", "")[:500] or
            state.get("llm_response", "") or
            ""
        )

    if respuesta:
        # Pasamos tema_actual (seteado por context_manager) para que
        # _resolver_referencia_anaforica pueda distinguir el tipo de
        # payload del turno anterior (shell vs web vs vision, etc).
        registrar_turno(
            mem, "jarvis", respuesta,
            sesion_id=state.get("sesion_id", ""),
            tema=state.get("tema_actual", ""),
        )

    print(f"\n{'─'*50}")
    return {"done": True, "final_response": respuesta or state.get("final_response")}


# ══════════════════════════════════════════════════════════════════════
# NODO: PLAN_SYNTHESIZER — Ornith sintetiza todos los resultados
# ══════════════════════════════════════════════════════════════════════

def node_plan_synthesizer(state: AetherState) -> dict:
    """
    Ornith recibe TODOS los datos crudos del plan y sintetiza la respuesta final.

    Para planes de 1 paso también pasa por aquí si la tool no generó
    final_response (web, shell, vision, codigo). Esto garantiza que
    siempre es Ornith quien razona y responde al usuario.
    """
    from core.agent.streaming import emit_token  # 👈 nuevo import

    orden = state["orden"]
    mem = state["mem"]
    plan_pasos = state.get("plan_pasos", [])
    plan_resultados = state.get("plan_resultados", [])

    print("\n🔮 [PLAN SYNTHESIZER]: Ornith sintetizando resultados...")

    # Construir contexto con todos los datos crudos disponibles
    contexto_datos = ""

    for i, (paso, resultado) in enumerate(zip(plan_pasos, plan_resultados)):
        tool = paso.get("tool", "?") if isinstance(paso, dict) else "?"
        contexto_datos += f"\n\n[DATOS - Paso {i+1} ({tool})]:\n{str(resultado)[:2000]}"

    if not contexto_datos:
        if state.get("web_results"):
            contexto_datos += f"\n\n[DATOS WEB]:\n{state['web_results'][:3000]}"
        if state.get("shell_output"):
            contexto_datos += f"\n\n[SALIDA SHELL]:\n{state['shell_output']}"
        if state.get("vision_result"):
            contexto_datos += f"\n\n[DESCRIPCIÓN VISUAL]:\n{state['vision_result']}"

    tokens = []
    def _on_token(t):
        tokens.append(t)
        emit_token(t)          # 👈 antes era print(t, end="", flush=True) (+ el print del prefijo)

    raw = _llm_chat(
        system=_system_prompt_sintesis(mem, state),
        user=(
            f"El usuario pidió: {orden}\n\n"
            f"Datos recopilados por las herramientas:{contexto_datos}\n\n"
            "Analiza los datos y responde al usuario de forma clara y útil en español. "
            "No menciones los pasos internos ni el proceso técnico, solo el resultado."
        ),
        on_token=_on_token,
    )

    # ya no hace falta el `if tokens: print()` de acá abajo, se borra

    reasoning, respuesta = _parse_ornith_thinking(raw)
    if reasoning:
        print(f"\n🧠 [Ornith thinking (síntesis)]: {reasoning[:200]}{'...' if len(reasoning)>200 else ''}")

    return {
        "final_response": respuesta,
        "llm_response": respuesta,
        "messages": [AIMessage(content=respuesta)],
        "plan_activo": False,
        "_ornith_reasoning": reasoning,
    }

    tokens = []
    def _on_token(t):
        if not tokens:
            print("\n🎙️  Aether: ", end="", flush=True)
        tokens.append(t)
        print(t, end="", flush=True)

    raw = _llm_chat(
        system=_system_prompt_sintesis(mem, state),
        user=(
            f"El usuario pidió: {orden}\n\n"
            f"Datos recopilados por las herramientas:{contexto_datos}\n\n"
            "Analiza los datos y responde al usuario de forma clara y útil en español. "
            "No menciones los pasos internos ni el proceso técnico, solo el resultado."
        ),
        on_token=_on_token,
    )

    if tokens:
        print()

    # Always parse Ornith <think> in synthesis
    reasoning, respuesta = _parse_ornith_thinking(raw)
    if reasoning:
        print(f"\n🧠 [Ornith thinking (síntesis)]: {reasoning[:200]}{'...' if len(reasoning)>200 else ''}")

    return {
        "final_response": respuesta,
        "llm_response": respuesta,
        "messages": [AIMessage(content=respuesta)],
        "plan_activo": False,
        "_ornith_reasoning": reasoning,
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: EXTRACT (limpieza genérica antes de file_write)
# ══════════════════════════════════════════════════════════════════════

def node_extract(state: AetherState) -> dict:
    """
    Limpia/extrae el resultado del paso anterior antes de guardarlo.
    Genérico: NO asume dominio específico.
    """
    orden = state["orden"]
    plan_resultados = state.get("plan_resultados") or []
    contenido_crudo = plan_resultados[-1] if plan_resultados else ""

    if not str(contenido_crudo).strip():
        return {"final_response": ""}

    respuesta = _llm_chat(
        system=(
            "Eres un extractor de contenido. Tu única tarea es devolver el "
            "contenido que el usuario pidió, en texto plano y limpio.\n"
            "Reglas estrictas:\n"
            "- NO agregues comentarios, saludos, ni frases tipo 'aquí tienes'.\n"
            "- NO incluyas metadatos de búsqueda (snippets, URLs, títulos) salvo "
            "que el usuario los haya pedido explícitamente.\n"
            "- NO incluyas entidades HTML sin decodificar ni restos de markup.\n"
            "- NO agregues relleno editorial que no fue solicitado.\n"
            "- Si el contenido ya está limpio, devuélvelo tal cual.\n"
            "- Responde ÚNICAMENTE con el contenido final a guardar."
        ),
        user=(
            f"El usuario pidió: {orden}\n\n"
            f"Contenido crudo disponible:\n{str(contenido_crudo)[:6000]}\n\n"
            "Extrae y devuelve solo lo que corresponde guardar."
        ),
    )

    return {"final_response": respuesta.strip()}


# ══════════════════════════════════════════════════════════════════════
# NODO: FILE_WRITE
# ══════════════════════════════════════════════════════════════════════

def node_file_write(state: AetherState) -> dict:
    """
    Guarda contenido en un archivo. Toma el resultado del paso anterior
    (plan_resultados[-1]) como contenido.
    """
    orden = state["orden"]
    plan_resultados = state.get("plan_resultados") or []

    # NOTA: dentro del sub_estado que arma node_plan_executor, plan_index
    # AÚN apunta al paso que se está ejecutando ahora mismo (el incremento
    # a plan_index+1 ocurre recién después, en plan_executor). Antes este
    # nodo usaba `plan_index - 1`, lo que en un plan multi-tool leía los
    # args del paso ANTERIOR en vez de los propios (confirmado con
    # test_plan_index.py). Se corrige para usar el mismo patrón que
    # node_shell / _comando_explicito_del_paso: sin offset.
    plan_pasos = state.get("plan_pasos") or []
    plan_index = state.get("plan_index", 0)
    idx = plan_index if isinstance(plan_index, int) and plan_index >= 0 else 0
    paso_actual_args = {}
    if idx < len(plan_pasos):
        paso_actual_args = plan_pasos[idx].get("args") or {}
    filename = paso_actual_args.get("filename") or paso_actual_args.get("nombre")

    contenido = plan_resultados[-1] if plan_resultados else orden

    contenido_limpio = re.sub(r"\[SHELL\].*?\[/SHELL\]", "", str(contenido), flags=re.DOTALL).strip()
    if not contenido_limpio:
        contenido_limpio = str(contenido).strip()

    orden_para_nombre = f"{filename}\n{orden}" if filename else orden

    nombre, exito = escribir_archivo(orden_para_nombre, contenido_limpio)

    if not exito:
        return {
            "error_activo":   True,
            "error_mensaje":  f"No se pudo escribir el archivo '{nombre}'.",
            "error_contexto": "file_write",
        }

    msg = f"Guardado en {nombre}"
    return {
        "final_response": msg,
        "messages":        [AIMessage(content=msg)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: PLAN_EXECUTOR (ejecuta pasos del plan reutilizando nodos reales)
# ══════════════════════════════════════════════════════════════════════

_CAMPOS_RESULTADO_DEFAULT = (
    "shell_output",
    "vision_result",
    "web_results",
    "final_response",
    "llm_response",
)

# Para web: priorizar web_results (datos crudos) sobre final_response
_CAMPOS_RESULTADO_POR_TOOL = {
    "web":    ("web_results", "final_response", "llm_response"),
    "shell":  ("shell_output", "final_response", "llm_response"),
    "vision": ("vision_result", "final_response", "llm_response"),
    "codigo": ("shell_output", "final_response", "llm_response"),
    "mcp":    ("mcp_result", "final_response", "llm_response"),
}


def _extraer_resultado_paso(salida_nodo: dict, tool: str = "") -> str:
    """
    Extrae el string-resultado de la salida de un nodo.
    Prioriza datos crudos sobre síntesis LLM para que Ornith reciba
    la información real, no un resumen previo.
    """
    if not isinstance(salida_nodo, dict):
        return ""
    campos = _CAMPOS_RESULTADO_POR_TOOL.get(tool, _CAMPOS_RESULTADO_DEFAULT)
    for campo in campos:
        val = salida_nodo.get(campo)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _construir_orden_paso(instruccion: str, args: dict, plan_resultados: list[str]) -> str:
    """
    Construye la 'orden' que recibirá el nodo para este paso.
    Incluye args explícitos y contexto de resultados previos.
    """
    partes = [instruccion]

    if isinstance(args, dict):
        for clave in ("query", "command", "app"):
            val = args.get(clave)
            if isinstance(val, str) and val.strip() and val.strip() not in instruccion:
                partes.append(f"[{clave}] {val.strip()}")

    if plan_resultados:
        ctx = "\n".join(
            f"  - Paso {i + 1}: {str(r)[:200]}" for i, r in enumerate(plan_resultados)
        )
        partes.append(f"\n[CONTEXTO DE PASOS PREVIOS]\n{ctx}")

    return "\n".join(partes)


def _instruccion_de_paso(paso: dict) -> str | None:
    if not isinstance(paso, dict):
        return None
    return paso.get("instruccion") or None


def node_plan_executor(state: AetherState) -> dict:
    """
    Ejecuta UN paso del plan reutilizando el nodo real de la tool.
    Acumula datos crudos en plan_resultados para que Ornith los sintetice.
    """
    from core.agent.tool_registry import get_node_func

    plan_pasos      = state.get("plan_pasos") or []
    plan_index      = state.get("plan_index", 0)
    plan_resultados = list(state.get("plan_resultados") or [])

    if plan_index >= len(plan_pasos):
        return {"plan_activo": False}

    paso = plan_pasos[plan_index]
    tool = paso.get("tool", "text") if isinstance(paso, dict) else "text"
    args = paso.get("args", {}) if isinstance(paso, dict) else {}
    if not isinstance(args, dict):
        args = {}

    instruccion = _instruccion_de_paso(paso) or state.get("orden", "")

    total = len(plan_pasos)
    print(f"\n🔧 [PLAN EXECUTOR]: Paso {plan_index + 1}/{total} → tool={tool}")

    sub_estado = dict(state)
    sub_estado["orden"] = _construir_orden_paso(instruccion, args, plan_resultados)
    sub_estado["error_activo"]   = False
    sub_estado["error_mensaje"]  = ""
    sub_estado["final_response"] = None

    salida_nodo: dict = {}
    try:
        node_func = get_node_func(tool)
        salida_nodo = node_func(sub_estado) or {}
    except Exception as e:
        print(f"   └─ ❌ Error ejecutando tool '{tool}': {e}")
        salida_nodo = {
            "error_activo":  True,
            "error_mensaje": f"Excepción en tool '{tool}': {e}",
            "error_contexto": tool,
        }

    resultado = _extraer_resultado_paso(salida_nodo, tool)
    if not resultado and salida_nodo.get("error_activo"):
        resultado = f"[ERROR] {salida_nodo.get('error_mensaje', 'fallo desconocido')}"
    plan_resultados.append(resultado)

    actualizacion: dict = {
        "plan_index":      plan_index + 1,
        "plan_resultados": plan_resultados,
        "error_intento":   0,
    }
    for campo in (
        "llm_response", "final_response", "shell_command", "shell_output", "shell_error",
        "web_results", "mcp_result", "vision_result", "_codigo_original", "_archivo_codigo",
        "error_activo", "error_mensaje", "error_contexto", "messages",
    ):
        if campo in salida_nodo:
            actualizacion[campo] = salida_nodo[campo]

    return actualizacion


# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLER — NODOS
# ══════════════════════════════════════════════════════════════════════

def node_error_diagnose(state: AetherState) -> dict:
    """
    Diagnostica el error usando el LLM + búsqueda web.
    Genera un fix propuesto y, para código, un diff legible.
    (Sigue usando LLM internamente porque necesita razonar para el fix.)
    """
    error_msg    = state.get("error_mensaje", "")
    error_ctx    = state.get("error_contexto", "shell")
    orden        = state["orden"]
    mem          = state["mem"]
    cmd_original = state.get("shell_command", "")
    intento      = state.get("error_intento", 0)

    print(f"\n🔬 [DIAGNÓSTICO] (intento #{intento + 1}): Analizando error...")

    if error_ctx == "codigo":
        query_web = f"python error {error_msg[:150]} solution"
    elif error_ctx == "launch":
        query_web = f"linux launch program {error_msg[:150]} alternative"
    else:
        query_web = f"{error_msg[:200]} linux zsh fix"

    print(f"🔍 [DIAGNÓSTICO]: Consultando web: '{query_web[:60]}...'")
    web_raw = buscar_web.invoke(query_web)

    urls = re.findall(r"URL:\s*(https?://\S+)", web_raw)
    contenido_url = ""
    fuente_web    = ""
    if urls:
        fuente_web = urls[0]
        print(f"📖 [DIAGNÓSTICO]: Leyendo {fuente_web}...")
        contenido_url = leer_url.invoke(fuente_web)[:2000]

    contexto_web = web_raw
    if contenido_url:
        contexto_web += f"\n\n[CONTENIDO WEB]\n{contenido_url}"

    if error_ctx == "codigo":
        codigo_original = state.get("_codigo_original", "")
        prompt_fix = (
            f"El usuario pidió: {orden}\n\n"
            f"Escribiste este código:\n```\n{codigo_original}\n```\n\n"
            f"Al ejecutarlo, salió este error:\n```\n{error_msg}\n```\n\n"
            f"Resultados de búsqueda web sobre el error:\n{contexto_web}\n\n"
            "Analiza el error, encuentra la causa exacta y reescribe SOLO el código corregido "
            "dentro de un bloque ```python ... ```. Sin explicaciones adicionales."
        )
    elif error_ctx == "launch":
        prompt_fix = (
            f"El usuario quiso lanzar: {orden}\n\n"
            f"Error obtenido: {error_msg}\n\n"
            f"Resultados de búsqueda web:\n{contexto_web}\n\n"
            "Si encuentras un Flatpak ID (como org.prismlauncher.PrismLauncher) o comando de lanzamiento, úsalo.\n"
            "Si el usuario mencionó 'flatpak', prioriza 'flatpak run ID'.\n"
            "NO propongas comandos de diagnóstico como 'flatpak list | grep'.\n"
            "Responde SOLO con el comando de lanzamiento exacto, en formato [SHELL]comando[/SHELL]."
        )
    else:
        prompt_fix = (
            f"El usuario pidió: {orden}\n\n"
            f"Se ejecutó: `{cmd_original}`\n\n"
            f"Error obtenido:\n```\n{error_msg}\n```\n\n"
            f"Resultados de búsqueda web:\n{contexto_web}\n\n"
            "Analiza el error y propón el comando corregido en formato [SHELL]comando[/SHELL]. "
            "Sin explicaciones adicionales."
        )

    print("\n🤖 [DIAGNÓSTICO]: Generando fix...")
    fix_raw = _llm_chat(system=_system_prompt(mem, state), user=prompt_fix)

    fix_propuesto = ""
    fix_diff      = ""

    if error_ctx == "codigo":
        bloques = re.findall(r"```(?:python|bash|sh)?\s*\n(.*?)\n```", fix_raw, re.DOTALL)
        if bloques:
            codigo_nuevo  = bloques[0].strip()
            fix_propuesto = codigo_nuevo
            original_lines = state.get("_codigo_original", "").splitlines(keepends=True)
            nuevo_lines    = codigo_nuevo.splitlines(keepends=True)
            diff_lines     = list(difflib.unified_diff(
                original_lines, nuevo_lines,
                fromfile="código_original", tofile="código_corregido", lineterm=""
            ))
            fix_diff = "".join(diff_lines) if diff_lines else "(sin cambios detectados)"
    else:
        cmd_fix       = extraer_comando_shell(fix_raw)
        fix_propuesto = cmd_fix or fix_raw.strip()[:300]

    # If we discovered a good flatpak launch via web, store it in mem so future searches find it
    if error_ctx == "launch" and "flatpak run " in fix_propuesto:
        m = re.search(r'flatpak run ([\w\.-]+)', fix_propuesto)
        if m:
            aid = m.group(1)
            mem.setdefault("flatpaks", {})
            mem["flatpaks"][aid.lower()] = aid
            last = aid.split(".")[-1].lower()
            mem["flatpaks"][last] = aid

    return {
        "fix_propuesto":  fix_propuesto,
        "fix_diff":       fix_diff,
        "fix_fuente_web": fuente_web,
        "web_results":    contexto_web,
        "error_intento":  intento + 1,
    }


def node_error_confirm(state: AetherState) -> dict:
    """
    Muestra diagnóstico al usuario y pide confirmación para aplicar el fix.
    Si el usuario cancela → marca done=True.
    """
    error_ctx     = state.get("error_contexto", "shell")
    error_msg     = state.get("error_mensaje", "")
    fix_propuesto = state.get("fix_propuesto", "")
    fix_diff      = state.get("fix_diff", "")
    fuente_web    = state.get("fix_fuente_web", "")
    intento       = state.get("error_intento", 1)

    print(f"\n{'═'*55}")
    print(f"🔬 DIAGNÓSTICO DEL ERROR (intento #{intento})")
    print(f"{'─'*55}")
    print(f"❌ Error:\n{error_msg[:400]}")
    print(f"{'─'*55}")

    if fuente_web:
        print(f"🌐 Fuente consultada: {fuente_web}")
        print(f"{'─'*55}")

    if error_ctx == "codigo" and fix_diff:
        print("📝 Cambios propuestos (diff):")
        for linea in fix_diff.splitlines():
            if linea.startswith("+") and not linea.startswith("+++"):
                print(f"\033[32m{linea}\033[0m")
            elif linea.startswith("-") and not linea.startswith("---"):
                print(f"\033[31m{linea}\033[0m")
            else:
                print(linea)
    else:
        print(f"🔧 Fix propuesto:\n{fix_propuesto}")

    print(f"{'═'*55}")

    confirmado = _confirmar_usuario("¿Aplicar este fix y reintentar?")

    if not confirmado:
        print("\n❌ [SISTEMA]: Fix rechazado por el usuario.")
        return {
            "final_response": f"Operación cancelada por el usuario tras {intento} intento(s).",
            "done":           True,
            "error_activo":   False,
        }

    return {"error_activo": True}


def node_error_retry(state: AetherState) -> dict:
    """
    Aplica el fix propuesto y ejecuta de nuevo.
    Si vuelve a fallar → reactiva error_activo para otro ciclo.
    """
    error_ctx    = state.get("error_contexto", "shell")
    fix_propuesto = state.get("fix_propuesto", "")
    archivo_cod  = state.get("_archivo_codigo", "/tmp/aether_code.py")
    mem          = state["mem"]
    orden        = state["orden"]

    if not fix_propuesto:
        return {
            "error_activo":   True,
            "error_mensaje":  "El diagnóstico no generó un fix válido.",
            "final_response": "",
        }

    if error_ctx == "codigo":
        with open(archivo_cod, "w", encoding="utf-8") as f:
            f.write(fix_propuesto)
        ext = archivo_cod[archivo_cod.rfind("."):]
        cmd = _cmd_para_extension(ext, archivo_cod) or f"python3 {archivo_cod}"
        print(f"\n⚙️  [RETRY CÓDIGO]: {cmd}")
        salida, hubo_error = ejecutar_comando(cmd)

    elif error_ctx == "launch":
        proposed = extraer_comando_shell(f"[SHELL]{fix_propuesto}[/SHELL]") or fix_propuesto
        # Clean trailing & and whitespace, then ensure exactly one trailing & for background
        proposed = proposed.strip().rstrip("&").strip()
        cmd = proposed
        if not cmd.endswith("&"):
            cmd += " &"
        print(f"\n⚙️  [RETRY LAUNCH]: {cmd}")
        salida, hubo_error = ejecutar_comando(cmd)

    else:
        cmd = fix_propuesto
        print(f"\n⚙️  [RETRY SHELL]: \033[1;33m{cmd}\033[0m")
        salida, hubo_error = ejecutar_comando(cmd)

    print(f"\n{'─'*50}\n{salida}\n{'─'*50}")
    registrar_comando(mem, orden, cmd)

    if hubo_error:
        print(f"\n❌ [RETRY]: Sigue fallando. Volviendo a diagnosticar...")
        return {
            "shell_command":    cmd,
            "shell_output":     salida,
            "shell_error":      True,
            "error_activo":     True,
            "error_mensaje":    salida,
            "_codigo_original": fix_propuesto if error_ctx == "codigo" else state.get("_codigo_original", ""),
        }

    print(f"\n✅ [RETRY]: Fix aplicado con éxito.")
    # For launch, make a clean success message with PID if we have it
    final_msg = salida or "Operación completada tras corrección automática."
    if error_ctx == "launch" and "PID" not in final_msg:
        m = re.search(r'(\d+)', salida or "")
        if m:
            final_msg = f"Proceso lanzado en segundo plano. PID: {m.group(1)}"
    return {
        "shell_command":  cmd,
        "shell_output":   salida,
        "shell_error":    False,
        "error_activo":   False,
        "final_response": final_msg,
        "messages":       [AIMessage(content=final_msg)],
    }


def node_error_fallback(state: AetherState) -> dict:
    """
    Estrategia alternativa cuando la web no da resultados útiles.
    """
    error_ctx = state.get("error_contexto", "shell")
    orden     = state["orden"]
    mem       = state["mem"]
    terminos  = _terminos_de_orden(orden)

    print(f"\n🔄 [FALLBACK]: Buscando alternativas sin web...")

    if error_ctx == "launch":
        salida_lista, _ = ejecutar_comando(
            "flatpak list --app --columns=application,name 2>/dev/null"
        )
        candidatos = []
        GENERIC = {"launcher", "app", "client", "game"}
        for linea in salida_lista.splitlines():
            partes = linea.split(None, 1)
            if len(partes) < 2:
                continue
            app_id, nombre = partes[0].strip(), partes[1].strip()
            last = app_id.split(".")[-1].lower()
            for t in terminos:
                tl = t.lower()
                parts_t = [p for p in re.findall(r"[a-z0-9]{4,}", tl) if p not in GENERIC]
                if not parts_t:
                    continue  # ignore pure generic like "launcher"
                hay_match_distintivo = any(p in (last + " " + nombre.lower() + " " + app_id.lower()) for p in parts_t)
                if (tl in last or tl in nombre.lower() or tl in app_id.lower()) and hay_match_distintivo:
                    candidatos.append((app_id, nombre))
                    break

        if candidatos:
            app_id, nombre = candidatos[0]
            print(f"\n🔄 [FALLBACK]: Candidato encontrado: {nombre} ({app_id})")
            mem.setdefault("flatpaks", {})
            mem["flatpaks"][app_id.lower()] = app_id
            mem["flatpaks"][app_id.split(".")[-1].lower()] = app_id
            return {
                "fix_propuesto":  f"flatpak run {app_id}",
                "fix_diff":       "",
                "fix_fuente_web": "(búsqueda local fuzzy)",
                "error_activo":   True,
            }

        for t in terminos:
            salida, err = ejecutar_comando(f"whereis {shlex.quote(t)} 2>/dev/null")
            if not err and salida.strip() and ":" in salida:
                rutas = salida.split(":", 1)[1].strip().split()
                if rutas:
                    return {
                        "fix_propuesto":  f"{rutas[0]} &",
                        "fix_diff":       "",
                        "fix_fuente_web": "(whereis local)",
                        "error_activo":   True,
                    }

    elif error_ctx == "shell":
        cmd = state.get("shell_command", "")
        cmd_simple = re.sub(r"\s+--?\w[\w-]*", "", cmd).strip()
        if cmd_simple and cmd_simple != cmd:
            return {
                "fix_propuesto":  cmd_simple,
                "fix_diff":       "",
                "fix_fuente_web": "(simplificación local)",
                "error_activo":   True,
            }

    elif error_ctx == "codigo":
        codigo_original = state.get("_codigo_original", "")
        fix_raw = _llm_chat(
            system=_system_prompt(mem, state),
            user=(
                f"Este código falla con dependencias externas:\n```\n{codigo_original}\n```\n"
                "Reescríbelo usando solo la librería estándar de Python. "
                "Devuelve SOLO el código dentro de ```python ... ```."
            ),
        )
        bloques = re.findall(r"```python\s*\n(.*?)\n```", fix_raw, re.DOTALL)
        if bloques:
            return {
                "fix_propuesto":  bloques[0].strip(),
                "fix_diff":       "",
                "fix_fuente_web": "(reescritura sin dependencias)",
                "error_activo":   True,
            }

    print("\n🤷 [FALLBACK]: No encontré alternativas.")
    return {
        "error_activo":   False,
        "final_response": (
            f"No pude completar la operación después de {state.get('error_intento', 0)} "
            "intento(s). No encontré alternativas disponibles."
        ),
        "done": True,
    }