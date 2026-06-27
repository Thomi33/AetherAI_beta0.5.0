"""
Configuración global y constantes del sistema Aether.
"""
import os
import warnings
import ast
from pathlib import Path

# =====================================================================
# ⚙️ CONFIGURACIÓN BASE
# =====================================================================
MODO_AUTONOMO   = True
OLLAMA_HOST     = "http://localhost:11434"
SEARXNG_URL     = "http://localhost:8081"
MODELO          = "deepseek-r1:14b" # ← modelo base para tareas de texto puro y tool calling (chat, análisis, etc.)
TIMEOUT_CMD     = 60
# Carpeta base de datos del agente (DB, logs, screenshots…).
# Cambiar SOLO esta ruta reubica toda la data del agente.
BASE_AETHER     = Path("/mnt/basurero/Aether")

# ─────────────────────────────────────────────────────────────────────
# 👁️  MODELO DE VISIÓN
# Debe ser un modelo multimodal instalado en Ollama.
# qwen3.5:9b es texto puro y NO soporta imágenes.
#
# Opciones comunes (instalar con: ollama pull <nombre>):
#   "llava:7b"        ← más común, buena calidad
#   "llava-phi3"      ← más rápido, menos RAM
#   "moondream"       ← muy ligero (~1.7GB)
#   "minicpm-v"       ← buena relación calidad/peso
#   "qwen2.5vl:7b"   ← si querés mantenerte en la familia Qwen
#
# Verificá los que tenés con: ollama list
# ─────────────────────────────────────────────────────────────────────
MODELO_VISION   =  "qwen3-vl:8b"  # ← CAMBIÁ según lo que tengas instalado

"""
PARCHE para core/config/settings.py

Agregar estas líneas DEBAJO de la sección "⚙️ CONFIGURACIÓN BASE"
(después de la línea MODELO_VISION = "minicpm-v:8b").
"""

# ─────────────────────────────────────────────────────────────────────
# 🔧 ORQUESTACIÓN: LangGraph (reemplaza CrewAI)
# ─────────────────────────────────────────────────────────────────────
# CONFIRMADO: qwen3.5:9b soporta tool calling nativo en Ollama
# (devuelve tool_calls estructurados — ver test del 22/06/2026).
# También es un modelo "thinking": separa razonamiento (`thinking`)
# de la respuesta final y de los tool_calls. El grafo usa ChatOllama
# (no la capa de compatibilidad OpenAI) para manejar esto correctamente.
#
# TOOL_CALLING_NATIVO = True  → el modelo decide function-calling
#                               de forma estructurada (JSON), sin
#                               parsear texto "Action:".
# TOOL_CALLING_NATIVO = False → modo legacy (_web_directo() de antes),
#                               por si necesitás revertir rápido.
TOOL_CALLING_NATIVO = True

RUTA_DB          = BASE_AETHER / "db"          / "memoria.db"
RUTA_LOGS        = BASE_AETHER / "logs"
RUTA_SCREENSHOTS = BASE_AETHER / "screenshots"
RUTA_EMBEDDINGS  = BASE_AETHER / "embeddings"
RUTA_BACKUPS     = BASE_AETHER / "backups"

# ─────────────────────────────────────────────────────────────────────
# 🧠 SUBSISTEMA DE MEMORIA CONTROLADO (core/memory/store)
# ─────────────────────────────────────────────────────────────────────
# DB ÚNICA de producción + DBs auxiliares. Toda escritura va por la write-API
# (core.memory.store) que valida con el guard de integridad y nunca cambia el
# esquema implícitamente. RUTA_DB (memoria.db) queda SOLO como fuente legacy
# para el importador de datos viejos.
MEMORIA_DIR   = BASE_AETHER / "db"
DB_CURRENT    = MEMORIA_DIR / "current.db"      # ← única DB de producción
DB_STAGING    = MEMORIA_DIR / "staging.db"      # ← pruebas antes de producción
SNAPSHOTS_DIR = MEMORIA_DIR / "snapshots"       # ← copias inmutables (rollback)
BACKUPS_DIR   = MEMORIA_DIR / "backups"         # ← respaldos pre-rollback

BASE_AETHER.mkdir(parents=True, exist_ok=True)

# Carpeta DEDICADA para los archivos que Aether crea con file_write cuando el
# usuario no especifica una ruta absoluta. Mantener los outputs separados del
# código del proyecto y en un lugar predecible (~/Aether).
CARPETA_AETHER = Path.home() / "Aether"

MAX_HISTORIAL = 100000


# ─────────────────────────────────────────────────────────────────────
# 🧠 CONTEXTO CONVERSACIONAL (memoria inyectada en el prompt)
# ─────────────────────────────────────────────────────────────────────
# Ventana de contexto del modelo en Ollama. Subirla permite inyectar MÁS
# turnos de conversación sin exceed_context_size_error, a costa de algo de
# RAM/VRAM y latencia. 16384 ya se usa en el resto del proyecto
# (builder/error_handler/graph), así que el equipo lo soporta.
# Bajalo a 8192 si tu equipo va justo de memoria.
NUM_CTX = 16384

# Máximo de turnos de conversación a inyectar (RAM → prompt). Subido de 50
# a 200. Se acota además por presupuesto de caracteres (abajo) para que un
# pico de turnos largos nunca desborde NUM_CTX.
MAX_TURNOS_CONTEXTO = 200

# Presupuesto de caracteres del bloque de conversación. Se incluyen los
# turnos MÁS RECIENTES hacia atrás hasta llegar a este tope (los más viejos
# se descartan). ~4 chars/token → 16000 ≈ 4000 tokens, holgado en NUM_CTX.
CONTEXTO_CONV_MAX_CHARS = 16000

# Turnos de conversación a inyectar DURANTE pasos de un plan multi-tool.
# El chat normal (planes de 1 paso) usa el historial completo (arriba), pero
# en un pipeline multi-tool el historial largo CONTAMINA cada paso (un paso
# "se acuerda" de tareas viejas y hace algo distinto). Por eso aquí se recorta
# fuerte: 0 = sin historial conversacional (cada paso se ejecuta con su propia
# instrucción + el contexto de pasos previos, que es lo único relevante).
MAX_TURNOS_CONTEXTO_PLAN = 0

# Turnos de conversación a inyectar en el camino de CHARLA (tool=text /
# modo_chat). El chat necesita continuidad, pero NO los 200 turnos completos:
# el historial largo hace que el modelo "siga" una tarea vieja (p.ej. un "Hola"
# que derivaba en un diagnóstico de hardware de una sesión anterior). Una
# ventana chica da contexto reciente sin arrastrar tareas viejas (anti-
# contaminación del chat). 0 = sin historial (no recomendado para chat).
MAX_TURNOS_CONTEXTO_CHAT = 10


# ─────────────────────────────────────────────────────────────────────
# ⚡ RENDIMIENTO DE OLLAMA (latencia ↓ a cambio de recursos)
# ─────────────────────────────────────────────────────────────────────
# keep_alive: cuánto mantener el modelo CARGADO en VRAM tras cada request.
# Es la mayor palanca de latencia: evita recargar el modelo (varios segundos)
# entre cada llamada (intent gate, node_text, synthesizer, resúmenes de tools).
# Valores: "30m", "1h" o "-1" (cargado para siempre). Con recursos de sobra,
# conviene mantenerlo caliente. Lo consume _llm_chat vía ollama.chat(keep_alive=).
OLLAMA_KEEP_ALIVE = "30m"

# Opciones de generación que se mergean en CADA llamada a _llm_chat (además de
# num_ctx). Pensadas para throughput cuando hay GPU/CPU de sobra:
#   num_batch  : tokens procesados por lote en el prefill (más alto = prompt
#                largo más rápido). 512 es un buen punto alto y seguro.
#   num_gpu    : capas a descargar a GPU. None = auto (ollama mete las que
#                entren). Poné 999 para FORZAR todas a GPU si tu VRAM da
#                (máxima velocidad). Si el modelo no entra entero, dejalo en
#                None o bajá el número.
#   num_thread : hilos de CPU para las capas en CPU. None = auto.
OLLAMA_GEN_OPTIONS = {
    "num_batch": 512,
    # "num_gpu": 999,    # ← descomentá para forzar offload total a GPU
    # "num_thread": 16,  # ← descomentá y ajustá a tus núcleos físicos
}

# Concurrencia del SERVIDOR ollama (no del cliente). El grafo es SECUENCIAL
# (una request a la vez), así que un solo modelo cargado alcanza. Estas vars
# de entorno solo importan si en el futuro se paralelizan pasos:
#   OLLAMA_NUM_PARALLEL      : requests concurrentes por modelo.
#   OLLAMA_MAX_LOADED_MODELS : modelos distintos cargados a la vez (texto+visión).
# Se setean en el entorno del servidor (no acá); ver explicación en el chat.
OLLAMA_NUM_PARALLEL = 4
OLLAMA_MAX_LOADED_MODELS = 2


# =====================================================================
# 🛠️ PARCHES DE COMPATIBILIDAD AST
# =====================================================================
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

for _nodo in ("NameConstant", "Str", "Num", "Bytes", "Ellipsis"):
    if not hasattr(ast, _nodo):
        setattr(ast, _nodo, type(_nodo, (ast.AST,), {}))


# =====================================================================
# 🔇 ENTORNO / TELEMETRÍA
# =====================================================================
# Compat OpenAI-sobre-Ollama para los clientes langchain (que además pasan la
# key explícitamente; estas vars son un fallback inofensivo). El motor
# LangGraph usa el cliente `ollama` nativo (apunta a localhost:11434).
os.environ.update({
    "OPENAI_API_KEY":  "ollama",
    "OLLAMA_API_BASE": f"{OLLAMA_HOST}",
})

import logging
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("root").setLevel(logging.ERROR)


# =====================================================================
# 🗣️ PALABRAS CLAVE DE INTENCIÓN
# =====================================================================

PALABRAS_CLAVE_ESCRITURA: set[str] = {
    "escribe", "crea", "genera", "redacta", "escribeme", "escríbeme",
    "archivo", "fichero", "guarda", "nota", "crea un archivo",
}

PALABRAS_CLAVE_VISION: list[str] = [
    "qué ves", "que ves", "describe", "mira", "captura", "pantalla",
    "screenshot", "imagen", "foto", "observa", "analiza la pantalla",
]

PALABRAS_CLAVE_LANZAR: set[str] = {
    "abre", "lanza", "inicia", "ejecuta", "arranca", "corre", "run",
    "start", "open", "abrir", "lanzar", "iniciar",
}

PALABRAS_CLAVE_WEB: list[str] = [
    "busca", "buscar", "search", "qué es", "que es", "quién es",
    "quien es", "cuánto", "cuanto", "precio", "noticia", "noticias",
    "clima", "tiempo en", "cómo se", "como se", "últimas", "ultimas",
    "wikipedia", "define", "explica qué", "explica que",
]