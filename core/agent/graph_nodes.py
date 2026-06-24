"""
Nodos del grafo LangGraph para Aether.

Cada función recibe AetherState y retorna un dict parcial con solo
los campos que modifica. LangGraph hace el merge automáticamente.

Nodos principales
-----------------
node_router         — detecta intención y llena `intent`
node_web            — buscar_web() → síntesis LLM
node_shell          — LLM genera comando → ejecutar_comando()
node_launch         — lanza programas (flatpak / PATH)
node_vision         — captura pantalla → ver_pantalla()
node_codigo         — LLM genera código → ejecuta
node_text           — respuesta directa via ollama streaming
node_memory         — procesa comandos de memoria sin LLM
node_finalize       — registra respuesta en DB, marca done=True

Nodos del error handler
-----------------------
node_error_diagnose — LLM diagnostica el error + busca en web
node_error_confirm  — muestra diagnóstico al usuario y pide confirmación
node_error_retry    — ejecuta el fix propuesto
node_error_fallback — estrategia alternativa sin web (PATH, variantes)

FIXES APLICADOS vs versión anterior
------------------------------------
FIX 1: buscar_web() llamada directa → buscar_web.invoke() (era ejecutado como tool)
FIX 2: leer_url()   llamada directa → leer_url.invoke()   (igual)
FIX 3: Shell injection en _buscar_flatpak_rapido → shlex.quote()
FIX 4: Shell injection en _buscar_en_path        → shlex.quote()
FIX 5: node_finalize NO registra turno usuario (ya lo hace procesar_orden_completo)
"""

import re
import difflib
import shlex
import unicodedata
import ollama

from langchain_core.messages import HumanMessage, AIMessage

from core.config.settings import MODELO, NUM_CTX
from core.memory.memory_manager import registrar_turno, registrar_comando, normalizar_mem
from core.memory.context_builder import construir_contexto_memoria
from core.agent.prompts import construir_backstory
from core.tools.shell_executor import ejecutar_comando
from core.tools.web_search import buscar_web
from core.tools.url_reader import leer_url
from core.tools.vision import ver_pantalla
from core.tools.flatpak_manager import buscar_flatpak_en_memoria
from core.parser.shell_parser import extraer_comando_shell

from core.agent.graph_state import AetherState


# ══════════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ══════════════════════════════════════════════════════════════════════

def _llm_chat(system: str, user: str, on_token=None) -> str:
    """
    Llamada directa a ollama con streaming opcional.
    
    FIX: num_ctx configurable (settings.NUM_CTX) para evitar
    exceed_context_size_error cuando el contexto de memoria es largo
    (ahora hasta 200 turnos).
    """
    respuesta = ""
    for chunk in ollama.chat(
        model=MODELO,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        stream=True,
        options={"num_ctx": NUM_CTX},  # ← ventana de contexto configurable
    ):
        token = chunk["message"]["content"]
        if token and on_token:
            on_token(token)
        respuesta += token
    return respuesta


def _system_prompt(mem: dict) -> str:
    """Construye el system prompt completo con contexto de memoria."""
    return construir_backstory(construir_contexto_memoria(mem))


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

    Hace el routing insensible a acentos, que es la causa #1 de mis-routing
    (p.ej. 'muestrame tu memoria' sin tilde caía a 'text' y alucinaba).
    """
    if not isinstance(texto, str):
        return ""
    texto = texto.lower()
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


_KW_WEB = frozenset([
    "busca", "buscar", "qué es", "que es", "cómo funciona", "como funciona",
    "cuál es", "cual es", "cuánto", "cuanto", "versión", "version",
    "última versión", "ultima version", "noticias", "noticia", "novedades",
    "precio", "investiga", "wikipedia", "actualmente", "hoy", "reciente",
    "último", "ultimo", "última", "ultima", "encuentra información",
    "qué dice", "que dice", "qué pasó", "que paso", "qué ha pasado",
    "que ha pasado", "qué hay de nuevo", "que hay de nuevo", "clima", "tiempo",
])

_KW_VISION = frozenset([
    "screenshot", "captura de pantalla", "captura la pantalla",
    "qué ves en", "que ves en", "qué contenido hay", "que contenido hay",
    "mira mi pantalla", "mira la pantalla", "observa mi pantalla",
    "observa la pantalla", "qué hay en pantalla", "que hay en pantalla",
    "en mi pantalla", "en la pantalla",
])

_KW_LAUNCH = frozenset(["ejecuta", "abre", "lanza", "inicia", "corre"])

_KW_MEMORY = frozenset([
    "muéstrame tu memoria", "tu memoria", "mi memoria", "qué recuerdas",
    "que recuerdas", "ver memoria", "mostrar memoria", "qué sabes de mí",
    "que sabes de mi", "qué tienes guardado", "que tienes guardado",
    "qué guardaste", "que guardaste",
    "borra la conversación", "limpia la memoria conversacional",
    "olvida la conversación", "borra los flatpaks", "olvida los flatpaks",
    "actualiza flatpaks", "mi nombre es", "años",
    "recuerda que", "anota que", "guarda que",
])

_KW_CODIGO = re.compile(
    r"\b(escribe|crea|genera|programa|script|funcion|clase|implementa"
    r"|codigo|python|java|bash|html|css|javascript)\b",
    re.IGNORECASE,
)

_STOP_WORDS = frozenset([
    "el", "la", "los", "las", "un", "una", "por", "favor", "me",
    "con", "sin", "en", "de", "del", "al",
])

# Variantes normalizadas (sin acentos) de cada set, para matching robusto.
_KW_WEB_N    = frozenset(_normalizar(k) for k in _KW_WEB)
_KW_VISION_N = frozenset(_normalizar(k) for k in _KW_VISION)
_KW_LAUNCH_N = frozenset(_normalizar(k) for k in _KW_LAUNCH)
_KW_MEMORY_N = frozenset(_normalizar(k) for k in _KW_MEMORY)


# ══════════════════════════════════════════════════════════════════════
# NODO: PLANNER (Tool Planning)
# ══════════════════════════════════════════════════════════════════════

def _detectar_intent_keywords(orden_lower: str) -> str | None:
    """
    Detección determinista de intención por keywords (sin LLM).
    Reutilizada por el planner (y el router deprecado).
    Prioridad: memory → vision → launch → web → codigo.
    Retorna el nombre de la tool o None si no hay match.
    """
    o = _normalizar(orden_lower)
    if any(x in o for x in _KW_MEMORY_N):
        return "memory"
    if any(x in o for x in _KW_VISION_N) and not any(x in o for x in _KW_WEB_N):
        return "vision"
    if any(x in o for x in _KW_LAUNCH_N):
        return "launch"
    if any(x in o for x in _KW_WEB_N):
        return "web"
    if _KW_CODIGO.search(o):
        return "codigo"
    return None


# Conectores e indicadores de que la tarea encadena varias acciones
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
    plan multi-tool. True si hay un conector de acciones Y al menos dos
    categorías de herramienta distintas implicadas.
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
    Intenta extraer y parsear el PRIMER objeto JSON ``{...}`` balanceado
    embebido en un texto. Útil cuando el LLM antepone prosa al JSON
    (p.ej. "Claro, voy a crear un plan: { ... }").

    Respeta llaves dentro de cadenas y secuencias de escape. Retorna el
    dict/objeto parseado o None si no encuentra JSON válido.
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


def _planner_llm(orden: str, mem: dict) -> list[dict] | None:
    """
    Pide al LLM un plan multi-tool. Retorna una lista de pasos normalizados
    al contrato {"tool","instruccion","args"} o None si no procede / falla.

    Se aísla en su propia función para poder mockearla en tests sin Ollama.
    """
    import json

    prompt = f"""Analiza si esta tarea requiere VARIAS herramientas encadenadas.

Herramientas disponibles:
- web: búsqueda en internet
- shell: ejecutar comandos de sistema
- launch: abrir aplicaciones
- vision: capturar/analizar pantalla
- codigo: generar y ejecutar código
- text: respuesta directa

Tarea: {orden}

Si necesita UNA sola herramienta, responde: {{"multi_tool": false}}

Si necesita VARIAS en secuencia, responde EXACTAMENTE este formato:
{{
  "multi_tool": true,
  "pasos": [
    {{"tool": "web", "instruccion": "buscar el precio de X", "args": {{"query": "precio X"}}}},
    {{"tool": "shell", "instruccion": "guardar el resultado en archivo", "args": {{"command": "echo ... > x.txt"}}}}
  ]
}}

Responde SOLO el JSON, sin explicaciones."""

    raw = ""
    try:
        raw = _llm_chat(system=_system_prompt(mem), user=prompt).strip()
    except Exception as e:
        print(f"   └─ [PLANNER]: fallo al invocar al LLM ({e})")
        return None

    # Aislar el JSON: soporta ```json ... ```, ``` ... ``` o prosa con un
    # objeto {...} embebido.
    contenido = raw
    if "```json" in contenido:
        contenido = contenido.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in contenido:
        contenido = contenido.split("```", 1)[1].split("```", 1)[0].strip()

    data = None
    try:
        data = json.loads(contenido)
    except Exception:
        # Reintento: extraer el primer objeto {...} balanceado del texto
        data = _extraer_json_objeto(contenido)

    if data is None:
        # PROBLEMA 4: loguear el CONTENIDO recibido (no solo la excepción)
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

    # Normalizar cada paso al contrato {tool, instruccion, args}
    pasos_norm: list[dict] = []
    for paso in data["pasos"]:
        if not isinstance(paso, dict):
            continue
        args = paso.get("args") if isinstance(paso.get("args"), dict) else {}
        instruccion = paso.get("instruccion")
        if not (isinstance(instruccion, str) and instruccion.strip()):
            # derivar instrucción de args o de la orden global
            instruccion = (
                args.get("query") or args.get("command") or args.get("app") or orden
            )
        pasos_norm.append({
            "tool": paso.get("tool"),
            "instruccion": instruccion,
            "args": args,
        })

    return pasos_norm or None


def _plan_activado(plan: list[dict], orden: str, mem: dict | None = None) -> dict:
    """Construye la actualización de estado que activa un plan.

    Si se pasa `mem`, se propaga en el estado para que TODOS los nodos
    downstream (memory, launch, web, shell, ...) reciban una memoria
    normalizada, incluso cuando el grafo se invoca con un `mem` crudo
    (p.ej. `{}`) sin pasar por crear_estado_inicial.
    """
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


def _clasificar_intent_llm(orden: str, mem: dict) -> str:
    """
    Clasificador de intención por LLM, usado SOLO como fallback cuando la
    detección por keywords no encuentra match.

    Antes, una orden sin keyword caía ciegamente a 'text' y el LLM inventaba
    datos (specs de hardware, noticias). Ahora el LLM elige la herramienta
    adecuada según su CAPACIDAD (incluida 'text' para charla/conocimiento
    general, y 'shell' para consultar el estado REAL del equipo).

    Devuelve un nombre de tool válido; 'text' si algo falla. Se aísla en su
    propia función para poder mockearla en tests sin Ollama.
    """
    from core.agent.tool_registry import TOOLS_VALIDAS

    try:
        clasif = _llm_chat(
            system=(
                "Eres un clasificador de intención. Responde SOLO con UNA palabra "
                "en minúsculas, sin explicaciones ni puntuación."
            ),
            user=(
                "Clasifica la orden del usuario en UNA de estas herramientas:\n"
                "- web: requiere información ACTUAL o externa (noticias, precios, "
                "versiones, eventos, clima).\n"
                "- shell: ejecutar un comando del sistema o consultar el estado REAL "
                "del equipo (RAM, discos, procesos, espacio).\n"
                "- launch: abrir o lanzar una aplicación.\n"
                "- vision: mirar o capturar la pantalla.\n"
                "- codigo: escribir y ejecutar código.\n"
                "- memory: ver o gestionar lo que Aether tiene GUARDADO del usuario "
                "(preferencias, notas, nombre).\n"
                "- text: charla o conocimiento general que puedes responder sin datos externos.\n\n"
                "Si dudas entre 'web' y 'text' y la respuesta NO depende de datos "
                "actuales, elige 'text'.\n\n"
                f"Orden: {orden}\n\nHerramienta:"
            ),
        ).strip().lower()
    except Exception as e:
        print(f"   └─ [PLANNER]: clasificador LLM falló ({e}); usando 'text'.")
        return "text"

    # Extraer la primera palabra que sea una tool válida
    for palabra in re.findall(r"[a-z]+", clasif):
        if palabra in TOOLS_VALIDAS:
            return palabra
    return "text"


def node_planner(state: AetherState) -> dict:
    """
    ÚNICA puerta de decisión del grafo (router fusionado aquí).

    Siempre produce un plan de ejecución (lista de pasos). Estrategia:

    1. Detección determinista por keywords → si hay intención clara y la
       tarea NO parece multi-tool, genera un plan de 1 paso con esa tool.
    2. Si la tarea parece encadenar acciones, pide un plan multi-tool al
       LLM y lo valida con validar_plan(); si es válido, se usa.
    3. Fallback seguro: plan de 1 paso (tool detectada o 'text').

    Nunca lanza: cualquier fallo cae al fallback de 1 paso.
    """
    from core.agent.tool_registry import validar_plan

    orden = state["orden"]
    orden_lower = orden.lower()
    # Normalizar la memoria en la ÚNICA puerta de entrada del grafo y
    # propagarla: garantiza que todo nodo downstream reciba el esquema
    # completo (preferencias/flatpaks/...) aunque el grafo se invoque con
    # un mem crudo. Elimina de raíz el KeyError 'preferencias'.
    mem = normalizar_mem(state.get("mem"))

    print("\n🧠 [PLANNER]: Decidiendo plan de ejecución...")

    intent_kw = _detectar_intent_keywords(orden_lower)
    multi = _parece_multitool(orden_lower)

    # ── Caso 1: intención clara y NO multi-tool → plan de 1 paso ──────
    if intent_kw and not multi:
        print(f"   └─ Plan de 1 paso (keyword): tool={intent_kw}")
        return _plan_activado([{"tool": intent_kw, "instruccion": orden}], orden, mem)

    # ── Caso 2: parece multi-tool → intentar plan del LLM ────────────
    if multi:
        print("   └─ Posible multi-tool, consultando LLM...")
        try:
            plan_llm = _planner_llm(orden, mem)
            if plan_llm:
                ok, errores = validar_plan(plan_llm)
                if ok:
                    print(f"   └─ Plan multi-tool válido: {len(plan_llm)} pasos")
                    return _plan_activado(plan_llm, orden, mem)
                print(f"   └─ Plan del LLM inválido: {errores}. Usando fallback.")
        except Exception as e:
            # Contrato: el planner NUNCA rompe el flujo → fallback seguro
            print(f"   └─ Error generando plan multi-tool ({e}). Usando fallback.")

    # ── Caso 3: fallback ─────────────────────────────────────────────
    # Si hubo un keyword claro (pero el plan multi-tool falló), úsalo.
    # Si NO hubo keyword, NO caer ciegamente a 'text' (eso causaba
    # alucinaciones): pedir al LLM que elija la tool por capacidad.
    if intent_kw:
        tool_fallback = intent_kw
        print(f"   └─ Plan de 1 paso (keyword): tool={tool_fallback}")
    else:
        tool_fallback = _clasificar_intent_llm(orden, mem)
        print(f"   └─ Plan de 1 paso (clasificador LLM): tool={tool_fallback}")
    return _plan_activado([{"tool": tool_fallback, "instruccion": orden}], orden, mem)


# ══════════════════════════════════════════════════════════════════════
# NODO: ROUTER  (DEPRECADO — conservado por compatibilidad; ya no está
# cableado en el grafo. La decisión la toma node_planner.)
# ══════════════════════════════════════════════════════════════════════

def node_router(state: AetherState) -> dict:
    """
    [DEPRECADO] Router original basado en keywords + LLM.

    Ya NO se usa en el grafo (node_planner es la única puerta de decisión).
    Se conserva temporalmente por compatibilidad y para poder revertir.
    """
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
# NODO: WEB
# ══════════════════════════════════════════════════════════════════════

def node_web(state: AetherState) -> dict:
    """
    Búsqueda web directa + síntesis LLM.
    Si los resultados mencionan una URL relevante, la lee también.
    """
    orden = state["orden"]
    mem   = state["mem"]

    print("\n🔍 [WEB]: Buscando...")
    # FIX 1: usar .invoke() — llamada directa ejecutaba la tool como comando zsh
    resultados = buscar_web.invoke(orden)

    # Intentar leer la primera URL encontrada para más contexto
    urls = re.findall(r"URL:\s*(https?://\S+)", resultados)
    contenido_url = ""
    if urls:
        print(f"📖 [WEB]: Leyendo {urls[0]}...")
        # FIX 2: igual que arriba para leer_url
        contenido_url = leer_url.invoke(urls[0])

    contexto_web = resultados
    if contenido_url:
        contexto_web += f"\n\n[CONTENIDO LEÍDO]\n{contenido_url[:3000]}"

    tokens: list[str] = []
    def _on_token(t):
        if not tokens:
            print("\n🎙️  Aether: ", end="", flush=True)
        tokens.append(t)
        print(t, end="", flush=True)

    respuesta = _llm_chat(
        system=_system_prompt(mem),
        user=(
            f"El usuario pregunta: {orden}\n\n"
            f"Resultados de búsqueda web:\n{contexto_web}\n\n"
            "Responde en español, claro y preciso, basándote en los resultados."
        ),
        on_token=_on_token,
    )
    if tokens:
        print()

    return {
        "web_results":    contexto_web,
        "llm_response":   respuesta,
        "final_response": respuesta,
        "messages":       [AIMessage(content=respuesta)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: SHELL
# ══════════════════════════════════════════════════════════════════════

def node_shell(state: AetherState) -> dict:
    """
    LLM genera un comando shell → se extrae → se ejecuta.
    Si hay error → activa error handler.
    """
    orden     = state["orden"]
    mem       = state["mem"]
    modo_auto = state.get("modo_autonomo", True)

    tokens: list[str] = []
    def _on_token(t):
        if not tokens:
            print("\n🤔 [AETHER]: ", end="", flush=True)
        tokens.append(t)
        print(t, end="", flush=True)

    llm_resp = _llm_chat(
        system=_system_prompt(mem),
        user=orden,
        on_token=_on_token,
    )
    if tokens:
        print()

    comando = extraer_comando_shell(llm_resp)

    # FIX: Si no se extrajo comando, reintentar con prompt explícito
    if not comando:
        print("⚠️  [SHELL]: No se detectó formato [SHELL]...[/SHELL], reintentando con prompt explícito...")
        llm_resp_retry = _llm_chat(
            system=_system_prompt(mem),
            user=(
                f"Genera SOLO el comando shell para ejecutar esta orden.\n"
                f"Orden: {orden}\n\n"
                f"IMPORTANTE: Devuelve el comando en formato [SHELL]comando[/SHELL]\n"
                f"Sin explicaciones adicionales."
            ),
        )
        comando = extraer_comando_shell(llm_resp_retry)
        if comando:
            print(f"   └─ Comando extraído tras reintento: {comando}")

    if not comando:
        # Aún no hay comando válido → respuesta de texto plano
        return {
            "llm_response":   llm_resp,
            "shell_command":  None,
            "final_response": llm_resp,
            "messages":       [AIMessage(content=llm_resp)],
        }

    # Mostrar comando y pedir confirmación si no es autónomo
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

    return {
        "llm_response":   llm_resp,
        "shell_command":  comando,
        "shell_output":   salida,
        "shell_error":    False,
        "error_activo":   False,
        "final_response": salida or "Comando ejecutado sin errores.",
        "messages":       [AIMessage(content=llm_resp)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: LAUNCH (programas gráficos y nativos)
# ══════════════════════════════════════════════════════════════════════

def _terminos_de_orden(orden: str) -> list[str]:
    """Extrae palabras clave de la orden, filtrando stop words y KW_LAUNCH."""
    stop = _STOP_WORDS | _KW_LAUNCH
    return [w for w in orden.lower().split() if w not in stop and len(w) > 2]


def _buscar_flatpak_rapido(terminos: list[str]) -> str | None:
    """
    Grep rápido en flatpak list sin LLM.
    FIX 3: shlex.quote() para evitar shell injection en el término de búsqueda.
    """
    for termino in reversed(terminos):
        # FIX 3: escapar el término antes de interpolarlo en el comando shell
        termino_seguro = shlex.quote(termino)
        salida, err = ejecutar_comando(
            f"flatpak list --app --columns=application,name 2>/dev/null "
            f"| grep -i {termino_seguro} | head -1"
        )
        if salida and not err:
            partes = salida.strip().split()
            if partes and "." in partes[0]:
                return partes[0]
    return None


def _buscar_en_path(terminos: list[str]) -> str | None:
    """
    Busca ejecutables en PATH del sistema sin LLM.
    FIX 4: shlex.quote() para evitar shell injection en el nombre del ejecutable.
    """
    for termino in reversed(terminos):
        # FIX 4: escapar el término antes de interpolarlo en el comando shell
        termino_seguro = shlex.quote(termino)
        salida, err = ejecutar_comando(f"command -v {termino_seguro} 2>/dev/null")
        if not err and salida.strip():
            return termino
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
    Lanza programas (flatpak o nativos en PATH) sin LLM cuando es posible.
    Si falla → activa error handler con contexto "launch".
    """
    orden    = state["orden"]
    mem      = state["mem"]
    terminos = _terminos_de_orden(orden)

    # 1. Caché de flatpaks en memoria
    app_id = buscar_flatpak_en_memoria(mem, orden)

    # 2. Grep en flatpak list
    if not app_id:
        print("\n🔍 [LAUNCH]: Buscando flatpak...")
        app_id = _buscar_flatpak_rapido(terminos)

    if app_id:
        cmd = f"flatpak run {app_id} &"
        print(f"\n🚀 [LAUNCH]: {app_id}")
        salida, hubo_error = ejecutar_comando(cmd)
        registrar_comando(mem, orden, cmd)
        if not hubo_error:
            return {
                "final_response": f"Lanzado {app_id}",
                "shell_command":  cmd,
                "shell_error":    False,
                "error_activo":   False,
            }
        return _launch_error(cmd, salida, orden)

    # 3. Ejecutable nativo en PATH
    print("\n🔍 [LAUNCH]: Buscando en PATH...")
    cmd_name = _buscar_en_path(terminos)
    if cmd_name:
        cmd = f"{cmd_name} &"
        print(f"\n🚀 [LAUNCH]: {cmd_name}")
        salida, hubo_error = ejecutar_comando(cmd)
        registrar_comando(mem, orden, cmd)
        if not hubo_error:
            return {
                "final_response": f"Lanzado {cmd_name}",
                "shell_command":  cmd,
                "shell_error":    False,
                "error_activo":   False,
            }
        return _launch_error(cmd, salida, orden)

    # 4. No encontrado → error handler para que busque alternativas
    print("\n❌ [LAUNCH]: Programa no encontrado. Activando diagnóstico...")
    return {
        "shell_command":  None,
        "shell_output":   f"No se encontró ningún ejecutable para: {orden}",
        "shell_error":    True,
        "error_activo":   True,
        "error_mensaje":  f"No se encontró ningún ejecutable para: {orden}",
        "error_contexto": "launch",
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: CÓDIGO
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
        ".java": None,   # compilación más compleja, no auto-ejecutar
    }.get(ext)


def node_codigo(state: AetherState) -> dict:
    """
    LLM genera código → se guarda en archivo temporal → se ejecuta si aplica.
    Si la ejecución falla → activa error handler con contexto "codigo".
    """
    orden     = state["orden"]
    mem       = state["mem"]
    modo_auto = state.get("modo_autonomo", True)

    tokens: list[str] = []
    def _on_token(t):
        if not tokens:
            print("\n🤔 [AETHER]: ", end="", flush=True)
        tokens.append(t)
        print(t, end="", flush=True)

    llm_resp = _llm_chat(
        system=_system_prompt(mem),
        user=orden,
        on_token=_on_token,
    )
    if tokens:
        print()

    # Extraer bloque de código
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

    return {
        "llm_response":   llm_resp,
        "shell_command":  cmd_ejecutar,
        "shell_output":   salida,
        "shell_error":    False,
        "error_activo":   False,
        "final_response": salida or "Código ejecutado sin errores.",
        "messages":       [AIMessage(content=llm_resp)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: VISION
# ══════════════════════════════════════════════════════════════════════

def node_vision(state: AetherState) -> dict:
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

    # Detectar si ver_pantalla_segura devolvió un mensaje de error interno
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

    print(f"\n🎙️  Aether: {descripcion}")
    return {
        "vision_result":  descripcion,
        "final_response": descripcion,
        "messages":       [AIMessage(content=descripcion)],
    }

# ══════════════════════════════════════════════════════════════════════
# NODO: TEXT
# ══════════════════════════════════════════════════════════════════════

def node_text(state: AetherState) -> dict:
    """Respuesta directa via ollama streaming para texto/conversación."""
    orden = state["orden"]
    mem   = state["mem"]

    tokens: list[str] = []
    def _on_token(t):
        if not tokens:
            print("\n🎙️  Aether: ", end="", flush=True)
        tokens.append(t)
        print(t, end="", flush=True)

    respuesta = _llm_chat(
        system=_system_prompt(mem),
        user=orden,
        on_token=_on_token,
    )
    if tokens:
        print()

    return {
        "llm_response":   respuesta,
        "final_response": respuesta,
        "messages":       [AIMessage(content=respuesta)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: MEMORY
# ══════════════════════════════════════════════════════════════════════

def node_memory(state: AetherState) -> dict:
    """Procesa comandos especiales de memoria sin LLM."""
    import json
    orden = state["orden"]
    # Defensivo: garantizar esquema completo antes de escrituras anidadas
    # como mem["preferencias"]["notas"].append(...) (evita KeyError si el
    # nodo se ejecuta con un mem parcial).
    mem   = normalizar_mem(state.get("mem"))
    o     = _normalizar(orden).strip()   # sin acentos → matching robusto
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

    FIX 5: NO registra el turno del usuario aquí.
    El turno "usuario" ya fue registrado por procesar_orden_completo()
    antes de invocar el grafo, por lo que registrarlo de nuevo causaba
    doble entrada en la tabla conversaciones.
    """
    mem      = state["mem"]
    respuesta = state.get("final_response", "")

    if respuesta:
        registrar_turno(mem, "jarvis", respuesta)

    print(f"\n{'─'*50}")
    return {"done": True}


# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLER — NODOS
# ══════════════════════════════════════════════════════════════════════

def node_error_diagnose(state: AetherState) -> dict:
    """
    Diagnostica el error usando el LLM + búsqueda web.
    Genera un fix propuesto y, para código, un diff legible.

    Flujo:
    1. Busca el error en web para obtener contexto real
    2. LLM analiza error + contexto web + código/comando original
    3. Propone fix concreto
    4. Si es código: genera diff
    """
    error_msg    = state.get("error_mensaje", "")
    error_ctx    = state.get("error_contexto", "shell")
    orden        = state["orden"]
    mem          = state["mem"]
    cmd_original = state.get("shell_command", "")
    intento      = state.get("error_intento", 0)

    print(f"\n🔬 [DIAGNÓSTICO] (intento #{intento + 1}): Analizando error...")

    # ── 1. Buscar el error en web ────────────────────────────────────
    if error_ctx == "codigo":
        query_web = f"python error {error_msg[:150]} solution"
    elif error_ctx == "launch":
        query_web = f"linux launch program {error_msg[:150]} alternative"
    else:
        query_web = f"{error_msg[:200]} linux zsh fix"

    print(f"🔍 [DIAGNÓSTICO]: Consultando web: '{query_web[:60]}...'")
    # FIX 1 (diagnose): usar .invoke() en vez de llamada directa
    web_raw = buscar_web.invoke(query_web)

    urls = re.findall(r"URL:\s*(https?://\S+)", web_raw)
    contenido_url = ""
    fuente_web    = ""
    if urls:
        fuente_web = urls[0]
        print(f"📖 [DIAGNÓSTICO]: Leyendo {fuente_web}...")
        # FIX 2 (diagnose): usar .invoke()
        contenido_url = leer_url.invoke(fuente_web)[:2000]

    contexto_web = web_raw
    if contenido_url:
        contexto_web += f"\n\n[CONTENIDO WEB]\n{contenido_url}"

    # ── 2. LLM diagnostica y genera fix ─────────────────────────────
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
            "Determina el nombre correcto del ejecutable o flatpak ID. "
            "Responde SOLO con el comando exacto para lanzarlo, en formato [SHELL]comando[/SHELL]."
        )
    else:  # shell
        prompt_fix = (
            f"El usuario pidió: {orden}\n\n"
            f"Se ejecutó: `{cmd_original}`\n\n"
            f"Error obtenido:\n```\n{error_msg}\n```\n\n"
            f"Resultados de búsqueda web:\n{contexto_web}\n\n"
            "Analiza el error y propón el comando corregido en formato [SHELL]comando[/SHELL]. "
            "Sin explicaciones adicionales."
        )

    print("\n🤖 [DIAGNÓSTICO]: Generando fix...")
    fix_raw = _llm_chat(system=_system_prompt(mem), user=prompt_fix)

    # ── 3. Extraer fix del output del LLM ───────────────────────────
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
        cmd = extraer_comando_shell(f"[SHELL]{fix_propuesto}[/SHELL]") or fix_propuesto
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
    return {
        "shell_command":  cmd,
        "shell_output":   salida,
        "shell_error":    False,
        "error_activo":   False,
        "final_response": salida or "Operación completada tras corrección automática.",
        "messages":       [AIMessage(content=salida or "Fix aplicado con éxito.")],
    }


def node_error_fallback(state: AetherState) -> dict:
    """
    Estrategia alternativa cuando la web no da resultados útiles.

    Casos por contexto:
    - launch : busca variantes del nombre en PATH y flatpak (fuzzy)
    - shell  : intenta versión simplificada del comando (quita flags)
    - codigo : reescritura sin dependencias externas via LLM
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
        for linea in salida_lista.splitlines():
            partes = linea.split(None, 1)
            if len(partes) < 2:
                continue
            app_id, nombre = partes
            for t in terminos:
                if t in nombre.lower() or t in app_id.lower():
                    candidatos.append((app_id, nombre))
                    break

        if candidatos:
            app_id, nombre = candidatos[0]
            print(f"\n🔄 [FALLBACK]: Candidato encontrado: {nombre} ({app_id})")
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
            system=_system_prompt(mem),
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


# ══════════════════════════════════════════════════════════════════════
# NODO: PLAN_EXECUTOR (ejecuta pasos del plan secuencialmente)
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# NODO: PLAN_EXECUTOR (ejecuta pasos del plan reutilizando nodos reales)
# ══════════════════════════════════════════════════════════════════════

# Campos del nodo de los que se extrae el "resultado" de un paso, en orden
# de preferencia.
_CAMPOS_RESULTADO = (
    "final_response",
    "shell_output",
    "vision_result",
    "web_results",
    "llm_response",
)


def _extraer_resultado_paso(salida_nodo: dict) -> str:
    """Extrae un string-resultado representativo de la salida de un nodo."""
    if not isinstance(salida_nodo, dict):
        return ""
    for campo in _CAMPOS_RESULTADO:
        val = salida_nodo.get(campo)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _construir_orden_paso(instruccion: str, args: dict, plan_resultados: list[str]) -> str:
    """
    Construye la 'orden' que recibirá el nodo reutilizado para este paso.

    Incluye los argumentos explícitos del paso y el contexto de resultados
    previos, de modo que el nodo (que deriva su comportamiento de `orden`)
    tenga toda la información necesaria para encadenar herramientas.
    """
    partes = [instruccion]

    # Adjuntar args explícitos relevantes (query/command/app) si aportan info
    if isinstance(args, dict):
        for clave in ("query", "command", "app"):
            val = args.get(clave)
            if isinstance(val, str) and val.strip() and val.strip() not in instruccion:
                partes.append(f"[{clave}] {val.strip()}")

    # Adjuntar contexto de pasos previos (acotado)
    if plan_resultados:
        ctx = "\n".join(
            f"  - Paso {i + 1}: {str(r)[:200]}" for i, r in enumerate(plan_resultados)
        )
        partes.append(f"\n[CONTEXTO DE PASOS PREVIOS]\n{ctx}")

    return "\n".join(partes)


def node_plan_executor(state: AetherState) -> dict:
    """
    Ejecuta UN paso del plan reutilizando el nodo real de la tool.

    En lugar de reimplementar la lógica de cada herramienta, construye un
    sub-estado por paso (con la instrucción del paso como `orden` y la
    memoria compartida) y despacha a la función de nodo registrada en
    TOOL_REGISTRY (node_text, node_web, node_shell, ...).

    - Acumula el resultado del paso en `plan_resultados`.
    - Propaga los campos de salida del nodo (incl. error_activo) al estado.
    - Captura excepciones por paso para no romper el plan completo.
    """
    from core.agent.tool_registry import get_node_func, _instruccion_de_paso

    plan_pasos      = state.get("plan_pasos") or []
    plan_index      = state.get("plan_index", 0)
    plan_resultados = list(state.get("plan_resultados") or [])

    # Defensa: índice fuera de rango → plan terminado
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

    # ── Construir sub-estado para el nodo reutilizado ────────────────
    sub_estado = dict(state)
    sub_estado["orden"] = _construir_orden_paso(instruccion, args, plan_resultados)
    # Resetear campos de error para que el paso parta limpio
    sub_estado["error_activo"]  = False
    sub_estado["error_mensaje"] = ""
    sub_estado["final_response"] = None

    # ── Despachar al nodo real ───────────────────────────────────────
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

    resultado = _extraer_resultado_paso(salida_nodo)
    if not resultado and salida_nodo.get("error_activo"):
        resultado = f"[ERROR] {salida_nodo.get('error_mensaje', 'fallo desconocido')}"
    plan_resultados.append(resultado)

    # ── Construir actualización de estado ────────────────────────────
    # Propagar campos relevantes del nodo (sin pisar bookkeeping del plan)
    actualizacion: dict = {
        "plan_index":      plan_index + 1,
        "plan_resultados": plan_resultados,
        # Presupuesto de reintentos fresco para cada paso nuevo
        "error_intento":   0,
    }
    for campo in (
        "llm_response", "final_response", "shell_command", "shell_output", "shell_error",
        "web_results", "vision_result", "_codigo_original", "_archivo_codigo",
        "error_activo", "error_mensaje", "error_contexto", "messages",
    ):
        if campo in salida_nodo:
            actualizacion[campo] = salida_nodo[campo]

    return actualizacion


# ══════════════════════════════════════════════════════════════════════
# NODO: PLAN_SYNTHESIZER (sintetiza todos los resultados del plan)
# ══════════════════════════════════════════════════════════════════════

def node_plan_synthesizer(state: AetherState) -> dict:
    """
    Sintetiza todos los resultados del plan multi-tool en una respuesta final.
    """
    orden = state["orden"]
    mem = state["mem"]
    plan_pasos = state.get("plan_pasos", [])
    plan_resultados = state.get("plan_resultados", [])
    
    print("\n🔮 [PLAN SYNTHESIZER]: Sintetizando resultados...")
    
    # Construir contexto con todos los pasos
    contexto_plan = ""
    for i, (paso, resultado) in enumerate(zip(plan_pasos, plan_resultados)):
        tool = paso.get("tool", "?") if isinstance(paso, dict) else "?"
        contexto_plan += f"\n\nPaso {i+1} ({tool}): {str(resultado)[:500]}"
    
    tokens = []
    def _on_token(t):
        if not tokens:
            print("\n🎙️  Aether: ", end="", flush=True)
        tokens.append(t)
        print(t, end="", flush=True)
    
    respuesta = _llm_chat(
        system=_system_prompt(mem),
        user=(
            f"El usuario pidió: {orden}\n\n"
            f"Ejecuté {len(plan_pasos)} herramientas secuencialmente:\n"
            f"{contexto_plan}\n\n"
            "Sintetiza una respuesta coherente basándote en todos los resultados."
        ),
        on_token=_on_token,
    )
    
    if tokens:
        print()
    
    return {
        "final_response": respuesta,
        "llm_response": respuesta,
        "messages": [AIMessage(content=respuesta)],
        "plan_activo": False,
    }
