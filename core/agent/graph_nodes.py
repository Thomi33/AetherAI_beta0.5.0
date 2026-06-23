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
node_text           — respuesta directa via ollama streaming
node_memory         — procesa comandos de memoria sin LLM
node_finalize       — registra turno en DB, marca done=True

Nodos del error handler
-----------------------
node_error_diagnose — LLM diagnostica el error + busca en web
node_error_confirm  — muestra diagnóstico al usuario y pide confirmación
node_error_retry    — ejecuta el fix propuesto
node_error_fallback — estrategia alternativa sin web (PATH, variantes)
"""

import re
import difflib
import ollama

from langchain_core.messages import HumanMessage, AIMessage

from core.config.settings import MODELO, OLLAMA_HOST
from core.memory.memory_manager import registrar_turno, registrar_comando
from core.memory.context_builder import construir_contexto_memoria
from core.agent.prompts import construir_backstory
from core.tools.shell_executor import ejecutar_comando
from core.tools.web_search import buscar_web
from core.tools.url_reader import leer_url
from core.tools.vision import ver_pantalla
from core.tools.flatpak_manager import buscar_flatpak_en_memoria
from core.parser.shell_parser import extraer_comando_shell

from graph_state import AetherState


# ══════════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ══════════════════════════════════════════════════════════════════════

def _llm_chat(system: str, user: str, on_token=None) -> str:
    """Llamada directa a ollama con streaming opcional."""
    respuesta = ""
    for chunk in ollama.chat(
        model=MODELO,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        stream=True,
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

_KW_WEB = frozenset([
    "busca", "buscar", "qué es", "que es", "cómo funciona", "como funciona",
    "cuál es", "cual es", "cuánto", "cuanto", "versión", "version",
    "última versión", "ultima version", "noticias", "precio", "investiga",
    "wikipedia", "actualmente", "hoy", "reciente", "último", "ultimo",
    "encuentra información", "qué dice", "que dice",
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
    "muéstrame tu memoria", "qué recuerdas", "ver memoria", "mostrar memoria",
    "borra la conversación", "limpia la memoria conversacional",
    "olvida la conversación", "borra los flatpaks", "olvida los flatpaks",
    "actualiza flatpaks", "mi nombre es", "tengo", "años",
    "recuerda que", "anota que", "guarda que",
])

_KW_CODIGO = re.compile(
    r"\b(escribe|crea|genera|programa|script|función|función|clase|implementa"
    r"|código|codigo|python|java|bash|html|css|javascript)\b",
    re.IGNORECASE,
)

_STOP_WORDS = frozenset([
    "el", "la", "los", "las", "un", "una", "por", "favor", "me",
    "con", "sin", "en", "de", "del", "al",
])


# ══════════════════════════════════════════════════════════════════════
# NODO: ROUTER
# ══════════════════════════════════════════════════════════════════════

def node_router(state: AetherState) -> dict:
    """
    Detecta la intención de la orden y decide qué nodo ejecutar.
    Prioridad: memory → vision → launch → web → shell/codigo → text
    """
    orden = state["orden"].lower()

    if any(x in orden for x in _KW_MEMORY):
        intent = "memory"
    elif any(x in orden for x in _KW_VISION) and not any(x in orden for x in _KW_WEB):
        intent = "vision"
    elif any(x in orden for x in _KW_LAUNCH):
        intent = "launch"
    elif any(x in orden for x in _KW_WEB):
        intent = "web"
    elif _KW_CODIGO.search(orden):
        intent = "codigo"
    else:
        intent = "text"

    print(f"\n🧭 [ROUTER]: intent={intent}")
    return {
        "intent": intent,
        "error_activo": False,
        "error_intento": state.get("error_intento", 0),
        "messages": [HumanMessage(content=state["orden"])],
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
    resultados = buscar_web(orden)

    # Intentar leer la primera URL encontrada para más contexto
    urls = re.findall(r"URL:\s*(https?://\S+)", resultados)
    contenido_url = ""
    if urls:
        print(f"📖 [WEB]: Leyendo {urls[0]}...")
        contenido_url = leer_url(urls[0])

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
        "web_results": contexto_web,
        "llm_response": respuesta,
        "final_response": respuesta,
        "messages": [AIMessage(content=respuesta)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: SHELL
# ══════════════════════════════════════════════════════════════════════

def node_shell(state: AetherState) -> dict:
    """
    LLM genera un comando shell → se extrae → se ejecuta.
    Si hay error → activa error handler.
    """
    orden      = state["orden"]
    mem        = state["mem"]
    modo_auto  = state.get("modo_autonomo", True)

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

    if not comando:
        # LLM respondió en texto plano sin comando shell
        return {
            "llm_response": llm_resp,
            "shell_command": None,
            "final_response": llm_resp,
            "messages": [AIMessage(content=llm_resp)],
        }

    # Mostrar comando y pedir confirmación si no es autónomo
    print(f"\n⚠️  [SHELL DETECTADO]: \033[1;33m{comando}\033[0m")
    if not modo_auto:
        if not _confirmar_usuario("¿Ejecutar este comando?"):
            return {
                "llm_response": llm_resp,
                "shell_command": comando,
                "shell_output": "",
                "shell_error": False,
                "final_response": "Ejecución cancelada.",
                "done": True,
            }

    print("\n⚙️  [EJECUTANDO EN ZSH...]")
    salida, hubo_error = ejecutar_comando(comando)
    print(f"\n{'─'*50}\n{salida}\n{'─'*50}")

    registrar_comando(mem, orden, comando)

    if hubo_error:
        print(f"\n❌ [ERROR SHELL]: Activando diagnóstico automático...")
        return {
            "llm_response": llm_resp,
            "shell_command": comando,
            "shell_output": salida,
            "shell_error": True,
            "error_activo": True,
            "error_mensaje": salida,
            "error_contexto": "shell",
            "messages": [AIMessage(content=llm_resp)],
        }

    return {
        "llm_response": llm_resp,
        "shell_command": comando,
        "shell_output": salida,
        "shell_error": False,
        "error_activo": False,
        "final_response": salida or "Comando ejecutado sin errores.",
        "messages": [AIMessage(content=llm_resp)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: LAUNCH (programas gráficos y nativos)
# ══════════════════════════════════════════════════════════════════════

def _buscar_flatpak_rapido(terminos: list[str]) -> str | None:
    """Grep rápido en flatpak list sin LLM."""
    for termino in reversed(terminos):
        salida, err = ejecutar_comando(
            f"flatpak list --app --columns=application,name 2>/dev/null "
            f"| grep -i '{termino}' | head -1"
        )
        if salida and not err:
            partes = salida.strip().split()
            if partes and "." in partes[0]:
                return partes[0]
    return None


def _buscar_en_path(terminos: list[str]) -> str | None:
    """Busca ejecutables en PATH del sistema sin LLM."""
    for termino in reversed(terminos):
        salida, err = ejecutar_comando(f"command -v '{termino}' 2>/dev/null")
        if not err and salida.strip():
            return termino
    return None


def _terminos_de_orden(orden: str) -> list[str]:
    """Extrae palabras clave de la orden, filtrando stop words y KW_LAUNCH."""
    stop = _STOP_WORDS | _KW_LAUNCH
    return [w for w in orden.lower().split() if w not in stop and len(w) > 2]


def node_launch(state: AetherState) -> dict:
    """
    Lanza programas (flatpak o nativos en PATH) sin LLM cuando es posible.
    Si falla → activa error handler con contexto "launch".
    """
    orden     = state["orden"]
    mem       = state["mem"]
    terminos  = _terminos_de_orden(orden)

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
            msg = f"Lanzado {app_id}"
            return {"final_response": msg, "shell_command": cmd,
                    "shell_error": False, "error_activo": False}
        # Flatpak encontrado pero falló al lanzar
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
            msg = f"Lanzado {cmd_name}"
            return {"final_response": msg, "shell_command": cmd,
                    "shell_error": False, "error_activo": False}
        return _launch_error(cmd, salida, orden)

    # 4. No encontrado → error handler para que busque alternativas
    print("\n❌ [LAUNCH]: Programa no encontrado. Activando diagnóstico...")
    return {
        "shell_command": None,
        "shell_output": f"No se encontró ningún ejecutable para: {orden}",
        "shell_error": True,
        "error_activo": True,
        "error_mensaje": f"No se encontró ningún ejecutable para: {orden}",
        "error_contexto": "launch",
    }


def _launch_error(cmd: str, salida: str, orden: str) -> dict:
    print(f"\n❌ [LAUNCH ERROR]: {salida[:200]}")
    return {
        "shell_command": cmd,
        "shell_output": salida,
        "shell_error": True,
        "error_activo": True,
        "error_mensaje": salida,
        "error_contexto": "launch",
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: CÓDIGO
# ══════════════════════════════════════════════════════════════════════

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
            "llm_response": llm_resp,
            "final_response": llm_resp,
            "messages": [AIMessage(content=llm_resp)],
        }

    codigo = bloques[0].strip()
    extension = _detectar_extension_codigo(llm_resp)
    archivo_tmp = f"/tmp/aether_code{extension}"

    with open(archivo_tmp, "w", encoding="utf-8") as f:
        f.write(codigo)

    print(f"\n📄 [CÓDIGO]: Guardado en {archivo_tmp}")

    # Preguntar si ejecutar
    if not modo_auto:
        if not _confirmar_usuario(f"¿Ejecutar {archivo_tmp}?"):
            return {
                "llm_response": llm_resp,
                "final_response": f"Código generado en {archivo_tmp} (no ejecutado).",
                "messages": [AIMessage(content=llm_resp)],
            }

    cmd_ejecutar = _cmd_para_extension(extension, archivo_tmp)
    if not cmd_ejecutar:
        return {
            "llm_response": llm_resp,
            "final_response": f"Código guardado en {archivo_tmp}. No sé cómo ejecutarlo automáticamente.",
            "messages": [AIMessage(content=llm_resp)],
        }

    print(f"\n⚙️  [EJECUTANDO]: {cmd_ejecutar}")
    salida, hubo_error = ejecutar_comando(cmd_ejecutar)
    print(f"\n{'─'*50}\n{salida}\n{'─'*50}")

    if hubo_error:
        print(f"\n❌ [ERROR CÓDIGO]: Activando diagnóstico automático...")
        return {
            "llm_response": llm_resp,
            "shell_command": cmd_ejecutar,
            "shell_output": salida,
            "shell_error": True,
            "error_activo": True,
            "error_mensaje": salida,
            "error_contexto": "codigo",
            # Guardamos el código original para poder hacer diff luego
            "_codigo_original": codigo,
            "_archivo_codigo": archivo_tmp,
            "messages": [AIMessage(content=llm_resp)],
        }

    return {
        "llm_response": llm_resp,
        "shell_command": cmd_ejecutar,
        "shell_output": salida,
        "shell_error": False,
        "error_activo": False,
        "final_response": salida or "Código ejecutado sin errores.",
        "messages": [AIMessage(content=llm_resp)],
    }


def _detectar_extension_codigo(texto: str) -> str:
    if "```python" in texto:  return ".py"
    if "```java"   in texto:  return ".java"
    if "```bash" in texto or "```sh" in texto: return ".sh"
    return ".py"  # default


def _cmd_para_extension(ext: str, archivo: str) -> str | None:
    return {
        ".py":   f"python3 {archivo}",
        ".sh":   f"bash {archivo}",
        ".java": None,  # compilación más compleja, no auto-ejecutar
    }.get(ext)


# ══════════════════════════════════════════════════════════════════════
# NODO: VISION
# ══════════════════════════════════════════════════════════════════════

def node_vision(state: AetherState) -> dict:
    """Captura pantalla y la analiza con el modelo de visión."""
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
    print(f"\n🎙️  Aether: {descripcion}")

    return {
        "vision_result": descripcion,
        "final_response": descripcion,
        "messages": [AIMessage(content=descripcion)],
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
        "llm_response": respuesta,
        "final_response": respuesta,
        "messages": [AIMessage(content=respuesta)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: MEMORY
# ══════════════════════════════════════════════════════════════════════

def node_memory(state: AetherState) -> dict:
    """Procesa comandos especiales de memoria sin LLM."""
    import json
    orden = state["orden"]
    mem   = state["mem"]
    o     = orden.lower().strip()
    resp  = ""

    if any(x in o for x in ["muéstrame tu memoria", "qué recuerdas", "ver memoria"]):
        resp = json.dumps(mem, ensure_ascii=False, indent=2)
        print(f"\n📋 [MEMORIA]:\n{resp}")

    elif any(x in o for x in ["borra la conversación", "olvida la conversación"]):
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
        "messages": [AIMessage(content=resp)],
    }


# ══════════════════════════════════════════════════════════════════════
# NODO: FINALIZE
# ══════════════════════════════════════════════════════════════════════

def node_finalize(state: AetherState) -> dict:
    """Registra el turno final en DB y marca done=True."""
    mem      = state["mem"]
    orden    = state["orden"]
    respuesta = state.get("final_response", "")

    registrar_turno(mem, "usuario", orden)
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
    error_msg   = state.get("error_mensaje", "")
    error_ctx   = state.get("error_contexto", "shell")
    orden       = state["orden"]
    mem         = state["mem"]
    cmd_original = state.get("shell_command", "")
    intento     = state.get("error_intento", 0)

    print(f"\n🔬 [DIAGNÓSTICO] (intento #{intento + 1}): Analizando error...")

    # ── 1. Buscar el error en web ────────────────────────────────────
    query_web = f"{error_msg[:200]} linux zsh fix"
    if error_ctx == "codigo":
        query_web = f"python error {error_msg[:150]} solution"
    elif error_ctx == "launch":
        query_web = f"linux launch program {error_msg[:150]} alternative"

    print(f"🔍 [DIAGNÓSTICO]: Consultando web: '{query_web[:60]}...'")
    web_raw = buscar_web(query_web)

    # Intentar leer la primera URL relevante
    urls = re.findall(r"URL:\s*(https?://\S+)", web_raw)
    contenido_url = ""
    fuente_web = ""
    if urls:
        fuente_web = urls[0]
        print(f"📖 [DIAGNÓSTICO]: Leyendo {fuente_web}...")
        contenido_url = leer_url(fuente_web)[:2000]

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
            codigo_nuevo = bloques[0].strip()
            fix_propuesto = codigo_nuevo
            # Generar diff legible
            original_lines = state.get("_codigo_original", "").splitlines(keepends=True)
            nuevo_lines    = codigo_nuevo.splitlines(keepends=True)
            diff_lines     = list(difflib.unified_diff(
                original_lines, nuevo_lines,
                fromfile="código_original", tofile="código_corregido", lineterm=""
            ))
            fix_diff = "".join(diff_lines) if diff_lines else "(sin cambios detectados)"
    else:
        cmd_fix = extraer_comando_shell(fix_raw)
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
    Si confirma → deja que node_error_retry ejecute.
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
        # Colorear diff básico en terminal
        for linea in fix_diff.splitlines():
            if linea.startswith("+") and not linea.startswith("+++"):
                print(f"\033[32m{linea}\033[0m")   # verde
            elif linea.startswith("-") and not linea.startswith("---"):
                print(f"\033[31m{linea}\033[0m")   # rojo
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
            "done": True,
            "error_activo": False,
        }

    return {"error_activo": True}   # continúa hacia node_error_retry


def node_error_retry(state: AetherState) -> dict:
    """
    Aplica el fix propuesto y ejecuta de nuevo.
    Si vuelve a fallar → reactiva error_activo para otro ciclo.
    """
    error_ctx     = state.get("error_contexto", "shell")
    fix_propuesto = state.get("fix_propuesto", "")
    archivo_cod   = state.get("_archivo_codigo", "/tmp/aether_code.py")
    mem           = state["mem"]
    orden         = state["orden"]

    if not fix_propuesto:
        return {
            "error_activo": True,
            "error_mensaje": "El diagnóstico no generó un fix válido.",
            "final_response": "",
        }

    if error_ctx == "codigo":
        # Reescribir archivo con código corregido
        with open(archivo_cod, "w", encoding="utf-8") as f:
            f.write(fix_propuesto)
        ext       = archivo_cod[archivo_cod.rfind("."):]
        cmd       = _cmd_para_extension(ext, archivo_cod) or f"python3 {archivo_cod}"
        print(f"\n⚙️  [RETRY CÓDIGO]: {cmd}")
        salida, hubo_error = ejecutar_comando(cmd)

    elif error_ctx == "launch":
        cmd = fix_propuesto if fix_propuesto.startswith("[SHELL]") else fix_propuesto
        cmd = extraer_comando_shell(f"[SHELL]{cmd}[/SHELL]") or fix_propuesto
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
            "shell_command":  cmd,
            "shell_output":   salida,
            "shell_error":    True,
            "error_activo":   True,
            "error_mensaje":  salida,
            # _codigo_original se actualiza al código corregido para el próximo diff
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
    - shell  : intenta versión simplificada del comando
    - codigo : intenta con versión más conservadora (menos dependencias)
    """
    error_ctx = state.get("error_contexto", "shell")
    orden     = state["orden"]
    mem       = state["mem"]
    terminos  = _terminos_de_orden(orden)

    print(f"\n🔄 [FALLBACK]: Buscando alternativas sin web...")

    if error_ctx == "launch":
        # Búsqueda fuzzy en flatpak list completo
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
            fix = f"flatpak run {app_id}"
            return {
                "fix_propuesto": fix,
                "fix_diff": "",
                "fix_fuente_web": "(búsqueda local fuzzy)",
                "error_activo": True,   # va a node_error_confirm
            }

        # Sin candidatos → buscar en which/whereis
        for t in terminos:
            salida, err = ejecutar_comando(f"whereis {t} 2>/dev/null")
            if not err and salida.strip() and ":" in salida:
                partes = salida.split(":", 1)
                rutas  = partes[1].strip().split() if len(partes) > 1 else []
                if rutas:
                    fix = f"{rutas[0]} &"
                    return {
                        "fix_propuesto": fix,
                        "fix_diff": "",
                        "fix_fuente_web": "(whereis local)",
                        "error_activo": True,
                    }

    elif error_ctx == "shell":
        # Simplificar el comando (quitar flags problemáticos)
        cmd = state.get("shell_command", "")
        cmd_simple = re.sub(r"\s+--?\w[\w-]*", "", cmd).strip()
        if cmd_simple and cmd_simple != cmd:
            return {
                "fix_propuesto": cmd_simple,
                "fix_diff": "",
                "fix_fuente_web": "(simplificación local)",
                "error_activo": True,
            }

    elif error_ctx == "codigo":
        # Pedir al LLM versión sin dependencias externas
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
                "fix_propuesto": bloques[0].strip(),
                "fix_diff": "",
                "fix_fuente_web": "(reescritura sin dependencias)",
                "error_activo": True,
            }

    # Fallback total: no hay alternativa
    print("\n🤷 [FALLBACK]: No encontré alternativas.")
    return {
        "error_activo": False,
        "final_response": (
            f"No pude completar la operación después de {state.get('error_intento', 0)} "
            "intento(s). No encontré alternativas disponibles."
        ),
        "done": True,
    }
