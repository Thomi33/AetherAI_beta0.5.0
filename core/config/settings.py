"""
Configuración global y constantes del sistema Aether.
"""
import os
import sys
import types
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
MODELO_LITELLM  = f"ollama/{MODELO}"
TIMEOUT_CMD     = 60
BASE_JAVIER     = Path("/mnt/basurero/Javier")

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

RUTA_DB          = BASE_JAVIER / "db"          / "memoria.db"
RUTA_LOGS        = BASE_JAVIER / "logs"
RUTA_SCREENSHOTS = BASE_JAVIER / "screenshots"
RUTA_EMBEDDINGS  = BASE_JAVIER / "embeddings"
RUTA_BACKUPS     = BASE_JAVIER / "backups"

BASE_JAVIER.mkdir(parents=True, exist_ok=True)

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


# =====================================================================
# 🛠️ PARCHES DE COMPATIBILIDAD AST
# =====================================================================
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

for _nodo in ("NameConstant", "Str", "Num", "Bytes", "Ellipsis"):
    if not hasattr(ast, _nodo):
        setattr(ast, _nodo, type(_nodo, (ast.AST,), {}))

if "pkg_resources" not in sys.modules:
    _mock = types.ModuleType("pkg_resources")
    class _Dist:
        version      = "0.11.2"
        project_name = "crewai"
    _mock.get_distribution = lambda _: _Dist()
    sys.modules["pkg_resources"] = _mock


# =====================================================================
# 🔇 SILENCIAR TELEMETRÍA
# =====================================================================
os.environ.update({
    "OPENAI_API_KEY":          "ollama",
    "CREWAI_TRACING_ENABLED":  "false",
    "CREW_SHARE_CREW":         "false",
    "OTEL_SDK_DISABLED":       "true",
    "CREWAI_TELEMETRY_OPTOUT": "true",
    "ANONYMOUS_TELEMETRY":     "false",
    "OLLAMA_API_BASE":         f"{OLLAMA_HOST}",
})

try:
    import crewai.telemetry as _ct
    _noop = lambda *a, **kw: None
    _ct.Telemetry.create_instance     = _noop
    _ct.Telemetry.task_started        = _noop
    _ct.Telemetry.task_ended          = _noop
    _ct.Telemetry.crew_execution_span = _noop
except Exception:
    pass

import logging
logging.getLogger("crewai").setLevel(logging.ERROR)
logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("litellm").setLevel(logging.ERROR)
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