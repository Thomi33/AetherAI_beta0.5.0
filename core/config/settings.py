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
MODELO          = "qwen3.5:9b"
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

RUTA_DB          = BASE_JAVIER / "db"          / "memoria.db"
RUTA_LOGS        = BASE_JAVIER / "logs"
RUTA_SCREENSHOTS = BASE_JAVIER / "screenshots"
RUTA_EMBEDDINGS  = BASE_JAVIER / "embeddings"
RUTA_BACKUPS     = BASE_JAVIER / "backups"

BASE_JAVIER.mkdir(parents=True, exist_ok=True)

MAX_HISTORIAL = 100000


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