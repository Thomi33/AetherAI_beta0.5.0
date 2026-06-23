"""
Servicio Aether: interfaz principal que orquesta todo el sistema.
Mantiene compatibilidad con la API existente de jarvis.py

CORRECCIONES vs versión anterior:
1. El agente SIEMPRE se construía con con_tools=False → web search nunca disponible
   AHORA: _necesita_web() detecta la intención y activa las tools cuando corresponde

2. Optimizaciones de la iteración anterior se mantienen:
   - _buscar_flatpak_rapido(): encuentra apps sin LLM
   - _analisis_rapido(): evita 2ª llamada LLM para comandos simples

OPTIMIZACIONES NUEVAS:
3. _lanzar_programa_rapido(): busca ejecutables en PATH del sistema sin LLM
   → programas nativos (firefox, code, vlc, etc.) se lanzan igual de rápido que flatpaks

4. _web_directo(): bypasea CrewAI para búsquedas web
   → llama la tool directamente + 1 sola llamada LLM para síntesis
   → antes: 3-5 llamadas LLM (planificar→buscar→leer→planificar→responder)
   → ahora: 1 llamada LLM (buscar→responder)
"""
import re
import json

from core.config.settings import MODELO
from core.memory.memory_manager import (
    registrar_turno,
    registrar_comando,
    guardar_memoria,
)
from core.tools.shell_executor import ejecutar_comando
from core.tools.flatpak_manager import (
    buscar_flatpak_en_memoria,
    intentar_lanzar_flatpak,
    actualizar_flatpaks,
)
from core.tools.file_writer import escribir_archivo
from core.tools.vision import ver_pantalla
from core.tools.web_search import buscar_web
from core.parser.shell_parser import extraer_comando_shell
from core.parser.response_parser import analizar_salida
from core.agent.builder import construir_agente
from core.agent.executor import crear_tarea, ejecutar_crew, _get_llm_fallback
from core.agent.prompts import construir_backstory
from core.agent.error_handler import (
    ejecutar_con_recuperacion,
    confirmar_autofix,
    MAX_INTENTOS_DEFAULT,
)
from core.memory.context_builder import construir_contexto_memoria

from core.utils.intent_utils import (
    contiene_escritura,
    contiene_vision,
    contiene_lanzar,
    necesita_web,
)


def _stream_directo(orden: str, mem: dict, on_token=None) -> str:
    """LLM directo con streaming real via ollama, sin CrewAI ni task description."""
    import ollama
    contexto = construir_contexto_memoria(mem)
    respuesta = ""
    for chunk in ollama.chat(
        model=MODELO,
        messages=[
            {"role": "system", "content": construir_backstory(contexto)},
            {"role": "user",   "content": orden},
        ],
        stream=True,
    ):
        token = chunk["message"]["content"]
        if token and on_token:
            on_token(token)
        respuesta += token
    return respuesta


# ─────────────────────────────────────────────
# Palabras clave para detección de intención
# ─────────────────────────────────────────────
PALABRAS_CLAVE_ESCRITURA = frozenset([
    "escribe", "crea", "guardar", "guarda", "crear",
    "archivo", "txt", "reporte", "documento", "informe", "json", "md",
])

# IMPORTANTE: usar frases completas, no palabras sueltas.
# "pantalla", "ves", "mira" solos son demasiado genéricos y
# colisionan con "busca las especificaciones de la pantalla..." etc.
PALABRAS_CLAVE_VISION = frozenset([
    "screenshot",
    "captura de pantalla",
    "captura la pantalla",
    "qué ves en",        # "qué ves en mi pantalla", "qué ves en la pantalla"
    "que ves en",
    "qué contenido hay", # "qué contenido hay en pantalla"
    "que contenido hay",
    "mira mi pantalla",
    "mira la pantalla",
    "observa mi pantalla",
    "observa la pantalla",
    "qué hay en pantalla",
    "que hay en pantalla",
    "en mi pantalla",
    "en la pantalla",
])

PALABRAS_CLAVE_LANZAR = frozenset([
    "ejecuta", "abre", "lanza", "inicia", "corre"
])

# Señales de que el usuario quiere buscar en internet
PALABRAS_CLAVE_WEB = frozenset([
    "busca", "buscar", "busca en internet", "busca en la web",
    "qué es", "que es", "cómo funciona", "como funciona",
    "cuál es", "cual es", "cuánto", "cuanto",
    "versión", "version", "última versión", "ultima version",
    "noticias", "precio", "investiga", "wikipedia",
    "actualmente", "hoy", "reciente", "último", "ultimo",
    "encuentra información", "qué dice", "que dice",
])

# Comandos cuya salida no necesita análisis LLM
_CMDS_SILENCIOSOS = (
    "flatpak run", "xdg-open", "gtk-launch", "nohup",
    "systemctl start", "systemctl stop",
)

# Stop words para búsqueda de ejecutables
_STOP_WORDS_LANZAR = frozenset([
    "el", "la", "los", "las", "un", "una", "por", "favor", "me",
    "por", "con", "sin", "en", "de", "del", "al",
])


def contiene_escritura(orden: str) -> bool:
    return bool(set(orden.lower().split()) & PALABRAS_CLAVE_ESCRITURA)


def contiene_vision(orden: str) -> bool:
    # Web tiene prioridad absoluta:
    # "busca las especificaciones de la pantalla..." → web, NO visión
    if necesita_web(orden):
        return False
    return any(x in orden.lower() for x in PALABRAS_CLAVE_VISION)


def contiene_lanzar(orden: str) -> bool:
    # Al usar intersección de sets, "corre" solo se activará si es una palabra suelta,
    # ignorando palabras compuestas como "corregirlo" o "correo".
    return bool(set(orden.lower().split()) & PALABRAS_CLAVE_LANZAR)


def necesita_web(orden: str) -> bool:
    """
    Detecta si la orden requiere búsqueda web.
    Activa con_tools=True en el agente cuando retorna True.
    """
    o = orden.lower()
    return any(x in o for x in PALABRAS_CLAVE_WEB)


# ─────────────────────────────────────────────
# Búsqueda rápida de flatpak sin LLM
# ─────────────────────────────────────────────
def _buscar_flatpak_rapido(orden: str) -> str | None:
    """
    Busca el app ID del flatpak con grep del sistema sin llamar al LLM.
    Retorna el app ID si lo encuentra, None si no.
    """
    stop_words = PALABRAS_CLAVE_LANZAR | _STOP_WORDS_LANZAR
    palabras = [
        w for w in orden.lower().split()
        if w not in stop_words and len(w) > 2
    ]
    if not palabras:
        return None

    for termino in reversed(palabras):
        cmd = (
            f"flatpak list --app --columns=application,name 2>/dev/null "
            f"| grep -i '{termino}' | head -1"
        )
        salida, hubo_error = ejecutar_comando(cmd)
        if salida and not hubo_error:
            partes = salida.strip().split()
            if partes and "." in partes[0]:
                return partes[0]

    return None


# ─────────────────────────────────────────────
# [NUEVO] Búsqueda rápida en PATH del sistema sin LLM
# ─────────────────────────────────────────────
def _lanzar_programa_rapido(orden: str) -> str | None:
    """
    Busca el nombre del ejecutable en PATH del sistema sin llamar al LLM.
    Usa 'command -v' para verificar existencia antes de lanzar.
    Retorna el nombre del comando si lo encuentra, None si no.

    Ejemplos: "abre firefox" → "firefox", "lanza code" → "code"
    """
    stop_words = PALABRAS_CLAVE_LANZAR | _STOP_WORDS_LANZAR
    palabras = [
        w for w in orden.lower().split()
        if w not in stop_words and len(w) > 2
    ]
    if not palabras:
        return None

    # Probamos de atrás para adelante: "abre el navegador firefox" → "firefox" primero
    for termino in reversed(palabras):
        salida, hubo_error = ejecutar_comando(f"command -v '{termino}' 2>/dev/null")
        if not hubo_error and salida.strip():
            return termino  # ejecutable confirmado en PATH

    return None


# ─────────────────────────────────────────────
# Análisis rápido sin LLM para casos comunes
# ─────────────────────────────────────────────
def _analisis_rapido(orden: str, comando: str, salida: str, hubo_error: bool) -> tuple[str | None, bool]:
    """
    Retorna (texto, necesita_llm).
    Si necesita_llm=False → usar texto directamente sin llamar a analizar_salida().
    """
    # Lanzamiento de apps → salida suele estar vacía
    if any(cmd in comando for cmd in _CMDS_SILENCIOSOS):
        if hubo_error:
            detalle = salida.strip()[:200] if salida.strip() else "sin detalles"
            return f"Hubo un error al lanzar la aplicación: {detalle}", False
        return "Aplicación iniciada.", False

    # Comando exitoso sin salida
    if not salida.strip() and not hubo_error:
        return "Hecho. El comando se ejecutó sin errores.", False

    # Error con salida corta
    if hubo_error and len(salida) < 300:
        return f"Error: {salida.strip()}", False

    # Casos complejos → necesita LLM
    return None, True


# ─────────────────────────────────────────────
# [NUEVO] Búsqueda web directa sin overhead de CrewAI
# ─────────────────────────────────────────────
def _web_directo(orden: str) -> str:
    """
    Bypasea CrewAI para búsquedas web: tool directa + 1 sola llamada LLM.

    Flujo anterior (CrewAI):
      LLM planifica → llama buscar_web → (opcional) llama leer_url → LLM sintetiza
      = 3 a 5 llamadas LLM

    Flujo nuevo:
      buscar_web() directamente → LLM sintetiza
      = 1 llamada LLM
    """
    try:
        print("\n🔍 [WEB DIRECTO]: Buscando sin overhead de CrewAI...")
        resultados = buscar_web(orden)

        llm = _get_llm_fallback()
        prompt = (
            f"El usuario pregunta: {orden}\n\n"
            f"Resultados de búsqueda web:\n{resultados}\n\n"
            "Responde en español de forma clara, precisa y concisa basándote "
            "exclusivamente en los resultados de búsqueda proporcionados. "
            "Si los resultados no son suficientes, indícalo."
        )
        return llm.invoke(prompt).content

    except Exception as e:
        # Fallback al flujo CrewAI si algo falla
        print(f"\n⚠️  [WEB DIRECTO]: Falló ({e}), usando CrewAI como fallback...")
        agente = construir_agente({}, con_tools=True)
        tarea = crear_tarea(orden, agente)
        return ejecutar_crew(agente, tarea)


# ─────────────────────────────────────────────
# API PÚBLICA
# ─────────────────────────────────────────────
def _procesar_orden(orden: str, mem: dict) -> str:
    """
    Función principal — mantiene compatibilidad total con la API existente.
    """
    con_tools = _necesita_web(orden)
    agente = construir_agente(mem, con_tools=con_tools)
    tarea = crear_tarea(orden, agente)
    respuesta = ejecutar_crew(agente, tarea)

    comando = extraer_comando_shell(respuesta)
    if comando:
        salida, _ = ejecutar_comando(comando)
        return salida

    return respuesta


def procesar_orden_completo(orden: str, mem: dict, modo_autonomo: bool = True) -> str:
    """
    Procesamiento COMPLETO de orden incluyendo lógica de memoria y transacciones.
    """
    registrar_turno(mem, "usuario", orden)

    # ATAJO 1: Órdenes de memoria (sin LLM)
    if _procesar_comando_memoria(orden, mem):
        return None

    # ATAJO 2: Visión de pantalla
    if _contiene_vision(orden):
        return _manejar_vision(orden)

    # ATAJO 3: Lanzar aplicaciones (sin LLM)
    if _contiene_lanzar(orden):
        app_id = None

        # 3a: Flatpak en caché de memoria
        app_id = buscar_flatpak_en_memoria(mem, orden)

        # 3b: Flatpak con grep (sin LLM)
        if not app_id:
            print("\n🔍 [BÚSQUEDA RÁPIDA]: Buscando flatpak sin LLM...")
            app_id = _buscar_flatpak_rapido(orden)
            if app_id:
                nombre_clave = [
                    w for w in orden.lower().split()
                    if w not in PALABRAS_CLAVE_LANZAR and len(w) > 2
                ]
                if nombre_clave:
                    mem["flatpaks"][nombre_clave[-1]] = app_id
                    guardar_memoria(mem)

        if app_id:
            print(f"\n🚀 [DIRECTO]: {app_id} encontrado. Lanzando sin LLM...")
            salida, hubo_error = ejecutar_comando(f"flatpak run {app_id} &")
            if not hubo_error:
                print(f"   ✅ Lanzado.")
            else:
                print(f"   ❌ Error: {salida}")
            registrar_comando(mem, orden, f"flatpak run {app_id}")
            registrar_turno(mem, "jarvis", f"Lanzado {app_id} directamente.")
            return f"Lanzado {app_id}"

        # 3c: Ejecutable nativo en PATH del sistema (sin LLM)
        print("\n🔍 [BÚSQUEDA RÁPIDA]: Buscando ejecutable en PATH...")
        cmd_path = _lanzar_programa_rapido(orden)
        if cmd_path:
            print(f"\n🚀 [DIRECTO]: '{cmd_path}' encontrado en PATH. Lanzando sin LLM...")
            salida, hubo_error = ejecutar_comando(f"{cmd_path} &")
            if not hubo_error:
                print(f"   ✅ Lanzado.")
            else:
                print(f"   ❌ Error: {salida}")
            registrar_comando(mem, orden, cmd_path)
            registrar_turno(mem, "jarvis", f"Lanzado {cmd_path} directamente.")
            return f"Lanzado {cmd_path}"

        # 3d: [NUEVO] AUTO-FIX — no se encontró en flatpaks ni en PATH.
        # Antes de rendirse, le preguntamos UNA VEZ al usuario si puede
        # buscar el nombre/paquete correcto por su cuenta (typos, nombres
        # de paquete distintos al binario, etc.)
        print("\n⚠️  [SISTEMA]: No encontré el programa en flatpaks ni en PATH.")
        if confirmar_autofix(f"lanzar el programa solicitado en: '{orden}'"):
            salida, exito, comando_final = ejecutar_con_recuperacion(
                comando_inicial=f"command -v {orden.split()[-1]}",
                contexto=(
                    f"El usuario pidió lanzar un programa con la orden: '{orden}'. "
                    "El nombre exacto del binario o paquete puede ser distinto al "
                    "que usó el usuario (typo, alias, nombre de paquete vs binario). "
                    "Buscá el nombre correcto del comando/paquete a instalar o ejecutar "
                    "en Arch Linux (pacman/yay/flatpak) y devolvé el comando final que "
                    "lo lanza, ej: 'nombre_correcto &' o 'flatpak run id.correcto &'."
                ),
                max_intentos=MAX_INTENTOS_DEFAULT,
                autorizado=True,
            )
            if exito:
                print(f"   ✅ Resuelto. Comando final: {comando_final}")
                registrar_comando(mem, orden, comando_final)
                registrar_turno(mem, "jarvis", f"Lanzado vía auto-fix: {comando_final}")
                return f"Lanzado (auto-fix): {comando_final}"
            else:
                print(f"   ❌ [AUTO-FIX]: No se pudo resolver. Último error: {salida}")
                registrar_turno(mem, "jarvis", f"Auto-fix falló al lanzar: {orden}")
                # Cae al flujo normal por si el LLM con contexto completo logra algo

    # ─────────────────────────────────────────
    # FLUJO NORMAL: Procesar con agente
    # ─────────────────────────────────────────
    con_tools = _necesita_web(orden)

    if con_tools:
        # [NUEVO] Búsqueda web directa: 1 llamada LLM en vez de 3-5
        respuesta = _web_directo(orden)
        print(f"\n🎙️  Aether: {respuesta}")
        registrar_turno(mem, "jarvis", respuesta)
        return respuesta

    # Flujo estándar para comandos/sistema/texto (sin web)
    print("\n🤔 [AETHER]: Pensando...", flush=True)

    _tokens: list[str] = []
    def _on_token(t: str) -> None:
        if not _tokens:
            print("\n🎙️  Aether: ", end="", flush=True)
        _tokens.append(t)
        print(t, end="", flush=True)

    respuesta = _stream_directo(orden, mem, _on_token)

    if _tokens:
        print()  # salto de línea al terminar el streaming

    comando = extraer_comando_shell(respuesta)

    if comando:
        respuesta_limpia = re.sub(
            r"\[SHELL\].*?\[/SHELL\]|```[\w]*\n.*?\n```",
            "", respuesta, flags=re.DOTALL
        ).strip()

        if respuesta_limpia and not _tokens:
            print(f"\n🎙️  Aether: {respuesta_limpia}")

        print(f"\n⚠️  [SHELL DETECTADO]:")
        print(f"   \033[1;33m{comando}\033[0m")

        ejecutar = modo_autonomo
        if not ejecutar:
            conf = input("¿Autorizar ejecución? [S/n]: ").strip().lower()
            ejecutar = conf in ("", "s", "si", "y", "yes")

        if ejecutar:
            print("\n⚙️  [EJECUTANDO EN ZSH...]")
            salida, hubo_error = ejecutar_comando(comando)

            # [NUEVO] AUTO-FIX: si falla, confirmación única y luego
            # reintento autónomo diagnosticando el error en la web.
            if hubo_error:
                print(f"\n⚠️  [ERROR DETECTADO]:\n{salida[:300]}")
                if confirmar_autofix(f"ejecutar: {comando}"):
                    salida, exito_fix, comando = ejecutar_con_recuperacion(
                        comando_inicial=comando,
                        contexto=f"Orden original del usuario: '{orden}'",
                        max_intentos=MAX_INTENTOS_DEFAULT,
                        autorizado=True,
                    )
                    hubo_error = not exito_fix

            print(f"\n{'─'*50}")
            print(salida)
            print(f"{'─'*50}")
            registrar_comando(mem, orden, comando)

            if "flatpak list" in comando and not hubo_error:
                intentar_lanzar_flatpak(mem, salida, orden)

            # Evitar 2ª llamada LLM para casos comunes
            analisis, necesita_llm = _analisis_rapido(orden, comando, salida, hubo_error)
            if necesita_llm:
                print("\n🤖 [ANALIZANDO RESULTADO...]")
                analisis = analizar_salida(orden, comando, salida)

            print(f"\n🎙️  Aether: {analisis}")
            registrar_turno(mem, "jarvis", analisis)
            return analisis
        else:
            print("\n❌ [SISTEMA]: Ejecución denegada de forma segura.")
            return "Ejecución cancelada."

    elif _contiene_escritura(orden):
        nombre_arch, ok = escribir_archivo(orden, respuesta)
        if ok:
            print(f"\n⚙️  [SISTEMA]: Archivo '{nombre_arch}' guardado.")
            print(f"\n🎙️  Aether: Informe plasmado en '{nombre_arch}'.")

            # [NUEVO] Si es código ejecutable y el usuario pidió probarlo,
            # corremos con auto-fix: confirmación única, luego reintentos
            # autónomos diagnosticando tracebacks/errores en la web.
            _quiere_probar = any(
                p in orden.lower() for p in ("prueba", "probar", "ejecuta", "corre", "testea")
            )
            if _quiere_probar and nombre_arch.endswith((".py", ".sh")):
                cmd_prueba = (
                    f"python3 {nombre_arch}" if nombre_arch.endswith(".py")
                    else f"bash {nombre_arch}"
                )
                print(f"\n🧪 [SISTEMA]: Probando '{nombre_arch}'...")
                salida_prueba, hubo_error = ejecutar_comando(cmd_prueba)

                if hubo_error:
                    print(f"\n⚠️  [ERROR AL PROBAR]:\n{salida_prueba[:300]}")
                    if confirmar_autofix(f"corregir y volver a probar '{nombre_arch}'"):
                        salida_prueba, exito_fix, _ = ejecutar_con_recuperacion(
                            comando_inicial=cmd_prueba,
                            contexto=(
                                f"Código generado en '{nombre_arch}' para la orden: "
                                f"'{orden}'. El error es del script, no del comando "
                                "que lo invoca — el fix debe reescribir el contenido "
                                f"de {nombre_arch} y luego volver a ejecutarlo con "
                                f"'{cmd_prueba}'."
                            ),
                            max_intentos=MAX_INTENTOS_DEFAULT,
                            autorizado=True,
                        )
                        if exito_fix:
                            print(f"   ✅ '{nombre_arch}' corregido y ejecutado con éxito.")
                        else:
                            print(f"   ❌ No se pudo corregir automáticamente: {salida_prueba[:200]}")
                else:
                    print(f"   ✅ Prueba exitosa:\n{salida_prueba[:300]}")
        else:
            print(f"\n❌ [SISTEMA]: No pude escribir el archivo '{nombre_arch}'.")
        registrar_turno(mem, "jarvis", respuesta)
        return respuesta

    else:
        if not _tokens:  # CrewAI respondió directamente, sin streaming
            print(f"\n🎙️  Aether: {respuesta}")
        registrar_turno(mem, "jarvis", respuesta)
        return respuesta


def _manejar_vision(orden: str) -> str:
    """Maneja órdenes de captura de pantalla."""
    import time
    print("\n👁️  [Aether ACTIVANDO VISIÓN — Mueve el cursor al monitor deseado]")
    for _i in range(5, 0, -1):
        print(f"   ⏳ {_i}...", end="\r", flush=True)
        time.sleep(1)
    print("   📸 Capturando...                ")
    pregunta = orden if len(orden) > 10 else "Analiza esta imagen técnicamente. Lista todos los elementos visibles: texto, ventanas, programas abiertos y su contenido."
    descripcion = ver_pantalla(pregunta)
    print(f"\n🎙️  Aether: {descripcion}")
    return descripcion


def _procesar_comando_memoria(orden: str, mem: dict) -> bool:
    """
    Procesa comandos especiales de gestión de memoria.
    Retorna True si procesó algo, False si debe continuar con flujo normal.
    """
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
        print("\n🎙️  Aether: Caché de Flatpaks reiniciado. Redescubriré las apps al próximo uso.")
        return True

    m = re.search(r"(?:recuerda|anota|guarda)\s+(?:que\s+)?(.+)", orden, re.IGNORECASE)
    if m and any(x in o for x in ["recuerda", "anota", "guarda que"]):
        nota = m.group(1).strip()
        mem["preferencias"]["notas"].append(nota)
        mem["preferencias"]["notas"] = mem["preferencias"]["notas"][-10:]
        guardar_memoria(mem)
        print(f"\n🎙️  Aether: Anotado en mi memoria: «{nota}»")
        return True

    guardado = False

    m = re.search(r"mi nombre es ([A-Za-záéíóúÁÉÍÓÚñÑ]+)", orden, re.IGNORECASE)
    if m:
        nombre = m.group(1).strip().capitalize()
        mem["preferencias"]["nombre_usuario"] = nombre
        guardado = True
        print(f"\n🎙️  Aether: Nombre registrado en memoria permanente: {nombre}.")

    m = re.search(r"tengo (\d+) años", orden, re.IGNORECASE)
    if m:
        mem["preferencias"]["edad"] = int(m.group(1))
        guardado = True
        print(f"\n🎙️  Aether: Edad registrada: {m.group(1)} años.")

    if guardado:
        guardar_memoria(mem)
        return True

    return False