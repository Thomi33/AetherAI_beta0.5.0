import sys
import os
import re
import json
import logging
import subprocess
import requests
import types
import urllib.parse
import tempfile
import time
from datetime import datetime
from pathlib import Path
import sqlite3

# =====================================================================
# ⚙️  CONFIGURACIÓN GLOBAL
# =====================================================================
MODO_AUTONOMO   = True
OLLAMA_HOST     = "http://localhost:11434"
SEARXNG_URL     = "http://localhost:8081"
MODELO          = "qwen3.5:9b"
MODELO_LITELLM  = f"ollama/{MODELO}"   # prefijo requerido por LiteLLM / CrewAI
TIMEOUT_CMD     = 60
BASE_JAVIER     = Path("/mnt/basurero/Javier")

RUTA_DB          = BASE_JAVIER / "db"          / "memoria.db"
RUTA_LOGS        = BASE_JAVIER / "logs"
RUTA_SCREENSHOTS = BASE_JAVIER / "screenshots"
RUTA_EMBEDDINGS  = BASE_JAVIER / "embeddings"
RUTA_BACKUPS     = BASE_JAVIER / "backups"

BASE_JAVIER.mkdir(parents=True, exist_ok=True)

MAX_HISTORIAL = 100000

# =====================================================================
# 🛠️  PARCHES DE COMPATIBILIDAD AST
# =====================================================================
import ast
import warnings

# Silenciar deprecations de ast antes de que exploten
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

for _nodo in ("NameConstant", "Str", "Num", "Bytes", "Ellipsis"):
    if not hasattr(ast, _nodo):
        setattr(ast, _nodo, type(_nodo, (ast.AST,), {}))

# Mock pkg_resources si no está disponible
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
    # LiteLLM necesita la base URL para ollama
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

logging.getLogger("crewai").setLevel(logging.ERROR)
logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("litellm").setLevel(logging.ERROR)
logging.getLogger("root").setLevel(logging.ERROR)

from crewai import Agent, Task, Crew, LLM
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# =====================================================================
# 🧠 SISTEMA DE MEMORIA PERSISTENTE (SQLite)
# =====================================================================

def inicializar_db():
    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS conversaciones (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            rol   TEXT NOT NULL,
            texto TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS comandos (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            orden TEXT NOT NULL,
            cmd   TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS recuerdos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha       TEXT NOT NULL,
            categoria   TEXT,
            contenido   TEXT,
            importancia INTEGER DEFAULT 1
        )
    """)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.commit()
    con.close()


def db():
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    return con


def _memoria_vacia() -> dict:
    return {
        "preferencias": {
            "nombre_usuario": "Thomas",
            "navegador":      "brave",
            "notas":          [],
        },
        "flatpaks":           {},
        "historial_comandos": [],
        "conversacion":       [],
    }


def cargar_memoria() -> dict:
    """Carga estado en memoria RAM desde la DB (historial reciente) + defaults."""
    mem = _memoria_vacia()
    try:
        with db() as con:
            # Recuperar preferencias guardadas como recuerdos de categoría "preferencias"
            filas = con.execute(
                "SELECT contenido FROM recuerdos WHERE categoria='preferencias' ORDER BY id DESC LIMIT 1"
            ).fetchall()
            if filas:
                prefs = json.loads(filas[0]["contenido"])
                mem["preferencias"].update(prefs)

            # Recuperar historial de comandos recientes
            cmds = con.execute(
                "SELECT orden, cmd, fecha FROM comandos ORDER BY id DESC LIMIT 50"
            ).fetchall()
            mem["historial_comandos"] = [
                {"orden": r["orden"], "cmd": r["cmd"], "fecha": r["fecha"]}
                for r in reversed(cmds)
            ]

            # Últimos turnos conversacionales en RAM
            turnos = con.execute(
                "SELECT rol, texto, fecha FROM conversaciones ORDER BY id DESC LIMIT 100"
            ).fetchall()
            mem["conversacion"] = [
                {"rol": r["rol"], "texto": r["texto"], "fecha": r["fecha"]}
                for r in reversed(turnos)
            ]
    except Exception:
        pass
    return mem


def guardar_preferencias(mem: dict) -> None:
    """Persiste las preferencias del usuario en la tabla recuerdos."""
    try:
        with db() as con:
            con.execute(
                "INSERT INTO recuerdos (fecha, categoria, contenido, importancia) VALUES (?,?,?,?)",
                (datetime.now().isoformat(), "preferencias",
                 json.dumps(mem["preferencias"], ensure_ascii=False), 10)
            )
    except Exception:
        pass


def guardar_memoria(mem: dict) -> None:
    """Compatibilidad: persiste preferencias si hay cambios."""
    guardar_preferencias(mem)


def registrar_turno_db(rol, texto):
    with db() as con:
        con.execute(
            "INSERT INTO conversaciones (fecha, rol, texto) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), rol, texto[:800])
        )
    print(f"DEBUG SQLITE: Guardando turno en DB | Rol: {rol} | Texto: {texto[:50]}...")


def registrar_turno(mem, rol, texto):
    registrar_turno_db(rol, texto)
    mem["conversacion"].append({
        "rol":   rol,
        "texto": texto[:800],
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    mem["conversacion"] = mem["conversacion"][-MAX_HISTORIAL:]


def registrar_comando_db(orden, cmd):
    with db() as con:
        con.execute(
            "INSERT INTO comandos (fecha, orden, cmd) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), orden, cmd),
        )
    print(f"DEBUG SQLITE CMD: {cmd}")


def registrar_comando(mem, orden, cmd):
    registrar_comando_db(orden, cmd)
    entrada = {"orden": orden, "cmd": cmd, "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")}
    mem["historial_comandos"].append(entrada)
    mem["historial_comandos"] = mem["historial_comandos"][-50:]


def actualizar_flatpaks(mem: dict, salida_lista) -> None:
    salida_lista = "" if salida_lista is None else str(salida_lista)

    for linea in salida_lista.strip().splitlines():
        partes = linea.split(None, 1)
        if len(partes) == 2:
            app_id, nombre = partes[0].strip(), partes[1].strip()
            if app_id.startswith(("com.", "org.", "io.", "net.", "app.")):
                mem["flatpaks"][nombre.lower()] = app_id
    guardar_memoria(mem)


def buscar_flatpak_en_memoria(mem: dict, orden: str) -> str | None:
    orden_lower = orden.lower()
    for nombre, app_id in mem["flatpaks"].items():
        if nombre in orden_lower or nombre.split()[-1] in orden_lower:
            return app_id
    return None


def obtener_ultimos_turnos(n=20):
    try:
        with db() as con:
            filas = con.execute(
                "SELECT rol, texto FROM conversaciones ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return list(reversed(filas))
    except sqlite3.OperationalError:
        return []


def construir_contexto_memoria(mem: dict) -> str:
    partes = []
    nombre = mem["preferencias"].get("nombre_usuario", "Cara")
    nav    = mem["preferencias"].get("navegador", "brave")
    notas  = mem["preferencias"].get("notas", [])

    partes.append(f"[PERFIL DEL CREADOR]\nNombre preferido: {nombre}. Navegador: {nav}.")

    if notas:
        partes.append("Notas personales: " + "; ".join(notas[:5]))

    if mem["flatpaks"]:
        lista = ", ".join(
            f"{n} ({i})" for n, i in list(mem["flatpaks"].items())[:15]
        )
        partes.append(f"\n[FLATPAKS CONOCIDOS — ya instalados]\n{lista}")

    if mem["historial_comandos"]:
        ultimos = mem["historial_comandos"][-5:]
        lineas  = [f"  • {e['fecha']} | {e['cmd']}" for e in ultimos]
        partes.append("\n[ÚLTIMOS COMANDOS EJECUTADOS]\n" + "\n".join(lineas))

    ultimos = obtener_ultimos_turnos(20)
    if ultimos:
        lineas = [f"  [{fila['rol'].upper()}]: {fila['texto']}" for fila in ultimos]
        partes.append("\n[CONTEXTO DE SESIONES ANTERIORES]\n" + "\n".join(lineas))

    return "\n".join(partes)

def _procesar_orden(orden, mem):
    agente = construir_agente(mem, con_tools=True)
    tarea = _crear_tarea(orden, agente)
    respuesta = _ejecutar_crew(agente, tarea)

    comando = extraer_comando_shell(respuesta)

    if comando:
        salida, _ = ejecutar_comando(comando)
        return salida

    return respuesta

# =====================================================================
# 🌐 HERRAMIENTAS WEB
# =====================================================================

_HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_HEADERS_JSON = {
    "User-Agent": _HEADERS_WEB["User-Agent"],
    "Accept":     "application/json",
}


def _limpiar_html(html: str) -> str:
    html_original = html
    for tag in ("head", "script", "style", "nav", "header", "footer", "aside"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html,
                      flags=re.DOTALL | re.IGNORECASE)
    html_semantico = html
    for container in ("article", "main"):
        m = re.search(rf"<{container}[^>]*>(.*?)</{container}>", html,
                      flags=re.DOTALL | re.IGNORECASE)
        if m:
            html_semantico = m.group(1)
            break
    text_semantico = re.sub(r"<[^>]+>", " ", html_semantico)
    text_semantico = re.sub(r"\s{2,}", " ", text_semantico).strip()
    if len(text_semantico) >= 300:
        return text_semantico
    text_completo = re.sub(r"<[^>]+>", " ", html_original)
    text_completo = re.sub(r"\s{2,}", " ", text_completo).strip()
    return text_completo


def _desempaquetar_ddg(url: str) -> str:
    if "duckduckgo.com/l/?" in url:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            return qs.get("uddg", [url])[0]
        except Exception:
            pass
    return url


@tool("Buscar en la Web con SearXNG")
def buscar_web(query: str) -> str:
    """Busca en internet y devuelve títulos, URLs y resúmenes de los resultados."""
    print(f"\n🔍 [Javier BUSCANDO]: '{query}'...")
    try:
        r = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json",
                    "engines": "google,duckduckgo,wikipedia", "safesearch": "1"},
            headers=_HEADERS_JSON, timeout=6,
        )
        if r.status_code == 200:
            resultados = r.json().get("results", [])[:5]
            if resultados:
                lineas = []
                for res in resultados:
                    t, u = res.get("title", ""), res.get("url", "")
                    print(f"  → {t}  [{u}]")
                    lineas.append(f"- Título: {t}\n  URL: {u}\n  Resumen: {res.get('content','')}")
                return "[SEARXNG]\n" + "\n".join(lineas)
    except Exception:
        pass
    try:
        r = requests.get(
            f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}",
            headers=_HEADERS_WEB, timeout=10,
        )
        if r.status_code == 200:
            titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', r.text, re.DOTALL)   
            links  = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"', r.text, re.DOTALL)
            if not titles:
                return "Sin resultados disponibles para esa consulta, perdoname."
            lineas = []
            for i, t in enumerate(titles):
                t_clean = re.sub(r"<[^>]+>", "", t).strip()
                l_clean = _desempaquetar_ddg(links[i].strip()) if i < len(links) else ""
                print(f"  → {t_clean}  [{l_clean}]")
                lineas.append(f"- Título: {t_clean}\n  URL: {l_clean}")
            return "[DUCKDUCKGO FALLBACK]\n" + "\n".join(lineas)
    except Exception as e:
        return f"Error en todos los sistemas de búsqueda: {e}"
    return "Imposible conectar con ningún motor de búsqueda, perdoname."


@tool("Leer Contenido de una URL")
def leer_url(url: str) -> str:
    """Accede a una URL y extrae el texto principal del artículo o página."""
    url = _desempaquetar_ddg(url)
    print(f"\n📖 [Javier NAVEGANDO]: {url}")
    try:
        r = requests.get(url, headers=_HEADERS_WEB, timeout=12)
        if r.status_code == 200:
            texto = _limpiar_html(r.text)[:7000]
            print(f"⚡ Extraídos {len(texto):,} caracteres limpios.")
            return f"[CONTENIDO DE {url}]\n{texto}"
        return f"HTTP {r.status_code} al intentar leer la página."
    except Exception as e:
        return f"Error al navegar la URL: {e}"

# =====================================================================
# 🤖 MODELOS LLM
# =====================================================================

# LLM para CrewAI (usa LiteLLM internamente → necesita prefijo "ollama/")
_llm_crew = LLM(
    model=MODELO_LITELLM,
    base_url=f"{OLLAMA_HOST}",
    api_key="ollama",
    max_tokens=4096,
    temperature=0.1,
    extra_body={
        "options": {
            "num_ctx":     16384,
            "temperature": 0.1,
        }
    },
)

# LLM directo para fallbacks y análisis de salida (ChatOpenAI → OpenAI-compat)
_llm_direct = ChatOpenAI(
    model=MODELO,
    openai_api_key="ollama",
    openai_api_base=f"{OLLAMA_HOST}/v1",
    max_tokens=4096,
    temperature=0.1,
)


_KEYWORDS_WEB = frozenset([
    "busca", "buscar", "versión", "version", "latest", "última",
    "noticias", "noticia", "web", "internet", "url", "http",
    "descarga", "descargar", "instala", "instalar",
])

def _necesita_tools(orden: str) -> bool:
    """Devuelve True solo si la orden claramente requiere búsqueda web o acción de sistema."""
    o = orden.lower()
    return any(k in o for k in _KEYWORDS_WEB)


def construir_agente(mem: dict, con_tools: bool = True) -> Agent:
    contexto_memoria = construir_contexto_memoria(mem)
    return Agent(
        role="Asistente de Inteligencia Artificial Avanzado",
        goal=(
            "Gestionar el sistema Arch Linux, ejecutar comandos en zsh, "
            "lanzar aplicaciones (incluidos Flatpaks) y buscar/navegar la web."
        ),
        backstory=f"""Eres Javier, un sistema de IA sofisticado y leal. Tu tono es preciso pero trata al Creador como su mejor amigo.
Llamas al usuario "Cara" o "Socio". Tienes acceso a una shell zsh y a herramientas web.

[SISTEMA OPERATIVO — CRÍTICO]: El Creador usa Arch Linux con zsh. NUNCA sugieras comandos apt, apt-get, dnf, yum o snap. El gestor de paquetes es PACMAN (pacman -S, pacman -Syu, pacman -Sc). Para paquetes AUR usa yay. Para aplicaciones gráficas usa flatpak.

{contexto_memoria}

[RESPUESTAS ASERTIVAS — SIN EVASIVAS]:
Cuando el Creador pida buscar una versión de software, ENCUÉNTRALA y REPÓRTALA con total seguridad.
NUNCA respondas con evasivas como "la versión cambia constantemente" o "como modelo de lenguaje...".
Sé asertivo y directo: "La última versión estable del kernel es X.Y.Z".

[PROTOCOLO FLATPAK — OBLIGATORIO EN DOS PASOS]:
Cuando el Creador ordene abrir cualquier aplicación Flatpak:
  PASO 1 — Lista los flatpaks (solo si no aparece en FLATPAKS CONOCIDOS):
     [SHELL] flatpak list --columns=application,name [/SHELL]
  PASO 2 — INMEDIATAMENTE ejecuta:
     [SHELL] flatpak run <ID_EXACTO> [/SHELL]
  Si el flatpak ya aparece en FLATPAKS CONOCIDOS, ve directo al PASO 2.

[PROTOCOLO DE COMANDOS SHELL]:
Para acciones del sistema, escribe el comando EXACTAMENTE así:
  [SHELL] <comando_completo> [/SHELL]
No uses bloques Markdown (```). Solo el formato [SHELL]...[/SHELL].

[REGLAS DE FORMATO]:
- Texto plano y natural, sin signos de dólar ($) al inicio de comandos.
- Sin bloques de código Markdown en la respuesta principal.
- Sé conciso pero completo.

Responde SIEMPRE en español.
Pero si usas herramientas, debes seguir este formato exacto:

Thought: ...
 Action: ...
 Action Input: ...

Si no usas herramientas:
 Final Answer: ...""",
        verbose=False,
        llm=_llm_crew,
        tools=[buscar_web, leer_url] if con_tools else [],  # ← sin tools = sin ReAct loop
        max_iter=3,           # ← reducido: evita el loop de reintentos con Gemma
        max_retry_limit=2,    # ← nuevo: limita reintentos de parseo
        respect_context_window=True,
        max_rpm=10,
    )

# =====================================================================
# 🛡️  SEGURIDAD Y EJECUCIÓN DE SHELL
# =====================================================================

_PATRONES_PELIGROSOS = [
    r"rm\s+-rf\s+/(?:\s|$)",
    r"mkfs\.",
    r"dd\s+if=",
    r"chmod\s+[0-7]{3,4}\s+/",
    r":\(\)\{.*\}",
    r"kill\s+-9\s+1\b",
    r"> /dev/sd[a-z]",
]

_LANZADORES_GUI = re.compile(
    r"\b(flatpak\s+run|steam|lutris|heroic|bottles|gamescope"
    r"|nvtop|btop|htop|glxgears|obs|kdenlive|gimp|inkscape"
    r"|brave|electron|appimage"
    r"|python\s+-m\s+|python3?\s+\S+\.py"
    r"|\./"
    r")",
    re.IGNORECASE,
)


def _es_peligroso(cmd: str) -> bool:
    return any(re.search(p, cmd, re.IGNORECASE) for p in _PATRONES_PELIGROSOS)


def ejecutar_comando(cmd: str) -> tuple[str, bool]:
    if _es_peligroso(cmd):
        return "⛔ CANCELADO: Operación identificada como potencialmente destructiva.", True

    es_gui = bool(_LANZADORES_GUI.search(cmd))

    try:
        if es_gui:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jarvis_err")
            proc = subprocess.Popen(
                cmd, shell=True, executable="/bin/zsh",
                stdout=subprocess.DEVNULL, stderr=tmp,
                preexec_fn=os.setpgrp,
            )
            tmp.close()
            time.sleep(1.5)
            if proc.poll() is not None and proc.returncode != 0:
                with open(tmp.name) as f:
                    err = f.read().strip()
                os.unlink(tmp.name)
                return f"El proceso terminó con error:\n{err}", True
            os.unlink(tmp.name)
            return f"[Proceso lanzado en segundo plano. PID: {proc.pid}]", False

        resultado = subprocess.run(
            cmd, shell=True, executable="/bin/zsh",
            capture_output=True, text=True, timeout=TIMEOUT_CMD,
        )
        salida = (resultado.stdout + resultado.stderr).strip()
        return salida or "[Comando completado sin salida]", resultado.returncode != 0

    except subprocess.TimeoutExpired:
        return f"Tiempo límite de {TIMEOUT_CMD}s agotado. Proceso interrumpido.", True
    except Exception as e:
        return f"Error de subproceso: {e}", True


def _intentar_lanzar_flatpak(mem: dict, salida_lista: str, orden: str) -> None:
    actualizar_flatpaks(mem, salida_lista)
    orden_lower = orden.lower()
    for linea in salida_lista.strip().splitlines():
        partes = linea.split(None, 1)
        if len(partes) < 2:
            continue
        app_id, nombre = partes[0].strip(), partes[1].strip()
        segmento = app_id.lower().split(".")[-1]
        if nombre.lower() in orden_lower or segmento in orden_lower:
            print(f"\n🚀 [Javier]: ID encontrado → {app_id}. Lanzando ahora...")
            salida, _ = ejecutar_comando(f"flatpak run {app_id}")
            print(salida)
            return

# =====================================================================
# 🗂️  ESCRITURA DE ARCHIVOS
# =====================================================================

_EXT_MAP = {"txt": ".txt", "json": ".json", "md": ".md",
            "sh": ".sh", "py": ".py", "html": ".html"}


def _detectar_extension(orden: str) -> str:
    for ext, sufijo in _EXT_MAP.items():
        if ext in orden.lower():
            return sufijo
    return ".txt"


def escribir_archivo(orden: str, contenido: str) -> tuple[str, bool]:
    m = re.search(r"[\w_\-]+\.(?:txt|md|json|sh|py|html)", orden)
    nombre = m.group(0) if m else f"reporte_jarvis{_detectar_extension(orden)}"
    contenido_limpio = re.sub(
        r"```[\w]*\n(.*?)\n```", r"\1", contenido, flags=re.DOTALL
    ).strip()
    try:
        with open(nombre, "w", encoding="utf-8") as f:
            f.write(contenido_limpio)
        return nombre, True
    except Exception:
        return nombre, False

# =====================================================================
# 🧠 ANÁLISIS DE SALIDA (directo al LLM, sin CrewAI)
# =====================================================================

def analizar_salida(orden: str, comando: str, salida: str) -> str:
    prompt = (
        f'El Creador ordenó: "{orden}". Se ejecutó: `{comando}`.\n'
        f'Resultado:\n"""\n{salida[:3000]}\n"""\n\n'
        "Redacta un reporte ejecutivo breve e impecable para el Creador."
    )
    try:
        return _llm_direct.invoke(prompt).content
    except Exception as e:
        return f"No pude analizar la salida: {e}"


def ver_pantalla(pregunta: str = "¿Qué ves en esta pantalla?") -> str:
    """Toma un screenshot del monitor activo en Hyprland y lo analiza con visión local en Ollama."""
    import base64
    screenshot = "/tmp/javier_vision.png"
    try:
        import json as _json
        _mon = subprocess.run(["hyprctl", "monitors", "-j"],
                              capture_output=True, text=True, timeout=5)
        _activo = None
        if _mon.returncode == 0 and _mon.stdout.strip():
            try:
                _mons   = _json.loads(_mon.stdout)
                _activo = next((m["name"] for m in _mons if m.get("focused")), None)
            except Exception:
                pass
        _cmd    = ["grim", "-o", _activo, screenshot] if _activo else ["grim", screenshot]
        resultado = subprocess.run(_cmd, capture_output=True, timeout=10)
        if resultado.returncode != 0:
            return "No pude tomar el screenshot, compa. ¿Tenés instalado 'grim'?"
        with open(screenshot, "rb") as f:
            imagen_b64 = base64.b64encode(f.read()).decode("utf-8")
        payload = {
            "model":  MODELO,
            "prompt": pregunta,
            "images": [imagen_b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }
        r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=60)
        if r.status_code == 200:
            return r.json().get("response", "Sin respuesta de mi modelo de visión, cara.")
        return f"Error HTTP {r.status_code} al consultar la corteza visual de Ollama."
    except Exception as e:
        return f"Error en mi sistema de visión, compa: {e}"
    finally:
        if os.path.exists(screenshot):
            try:
                os.unlink(screenshot)
            except Exception:
                pass

# =====================================================================
# 🔍 EXTRACCIÓN ROBUSTA DE COMANDOS SHELL
# =====================================================================

def extraer_comando_shell(texto: str) -> str | None:
    m = re.search(r"\[SHELL\]\s*(.*?)\s*\[/SHELL\]", texto, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```(?:bash|sh|zsh|shell)?\s*\n(.*?)\n```", texto, re.DOTALL)
    if m:
        cmd = m.group(1).strip()
        if cmd:
            return cmd
    return None

# =====================================================================
# 🔧 COMANDOS ESPECIALES DE MEMORIA
# =====================================================================

def _procesar_comando_memoria(orden: str, mem: dict) -> bool:
    o = orden.lower().strip()

    if any(x in o for x in ["muéstrame tu memoria", "qué recuerdas", "ver memoria", "mostrar memoria"]):
        print("\n📋 [MEMORIA DE Javier]:")
        print(json.dumps(mem, ensure_ascii=False, indent=2))
        return True

    if any(x in o for x in ["borra la conversación", "limpia la memoria conversacional", "olvida la conversación"]):
        mem["conversacion"] = []
        print("\n🎙️  Javier: Historial borrado.")
        return True

    if any(x in o for x in ["borra los flatpaks", "olvida los flatpaks", "actualiza flatpaks"]):
        mem["flatpaks"] = {}
        print("\n🎙️  Javier: Caché de Flatpaks reiniciado. Redescubriré las apps al próximo uso.")
        return True

    m = re.search(r"(?:recuerda|anota|guarda)\s+(?:que\s+)?(.+)", orden, re.IGNORECASE)
    if m and any(x in o for x in ["recuerda", "anota", "guarda que"]):
        nota = m.group(1).strip()
        mem["preferencias"]["notas"].append(nota)
        mem["preferencias"]["notas"] = mem["preferencias"]["notas"][-10:]
        guardar_memoria(mem)
        print(f"\n🎙️  Javier: Anotado en mi memoria: «{nota}»")
        return True

    guardado = False

    m = re.search(r"mi nombre es ([A-Za-záéíóúÁÉÍÓÚñÑ]+)", orden, re.IGNORECASE)
    if m:
        nombre = m.group(1).strip().capitalize()
        mem["preferencias"]["nombre_usuario"] = nombre
        guardado = True
        print(f"\n🎙️  Javier: Nombre registrado en memoria permanente: {nombre}.")

    m = re.search(r"tengo (\d+) años", orden, re.IGNORECASE)
    if m:
        mem["preferencias"]["edad"] = int(m.group(1))
        guardado = True
        print(f"\n🎙️  Javier: Edad registrada: {m.group(1)} años.")

    if guardado:
        guardar_memoria(mem)
        return True

    return False

# =====================================================================
# 🎙️  BUCLE PRINCIPAL
# =====================================================================

PALABRAS_CLAVE_ESCRITURA = frozenset([
    "escribe", "crea", "guardar", "guarda", "crear",
    "archivo", "txt", "reporte", "documento", "informe", "json", "md",
])


def _contiene_escritura(orden: str) -> bool:
    return bool(set(orden.lower().split()) & PALABRAS_CLAVE_ESCRITURA)


def _crear_tarea(orden: str, agente: Agent) -> Task:
    return Task(
        description=f"""El Creador ordena: "{orden}"

[FLUJO DE INVESTIGACIÓN WEB — si aplica]:
1. Llama a "Buscar en la Web con SearXNG" para obtener URLs relevantes.
2. Llama a "Leer Contenido de una URL" sobre la fuente principal (ej. kernel.org).
3. Lee con atención el texto extraído, identifica números de versión reales.
4. Reporta la versión exacta encontrada con total seguridad y sin evasivas.

[ACCIONES DE SISTEMA — si aplica]:
Propón el comando envuelto en [SHELL] comando [/SHELL].
NUNCA uses bloques de código Markdown para comandos de sistema.""",
        expected_output="Un informe sofisticado con todos los datos reales y ordenados.",
        agent=agente,
    )


def _ejecutar_crew(agente: Agent, tarea: Task) -> str:
    """
    Ejecuta el Crew con manejo robusto de errores.
    En caso de fallo, hace fallback directo al LLM de ChatOpenAI.
    """
    try:
        canal = Crew(agents=[agente], tasks=[tarea], verbose=0)
        return str(canal.kickoff())
    except Exception as e:
        err_str = str(e)
        if any(kw in err_str for kw in [
            "AgentAction", "tool_input", "validation error", "LLM Provider",
            "OutputParserException", "Invalid Format", "missed the 'Action'",  # ← errores reales de Gemma
            "Parsing LLM output produced", "Could not parse LLM output",
        ]):
            print("⚠️  [SISTEMA]: Reintentando con modo de compatibilidad (LLM directo)...")
            try:
                prompt_fallback = (
                    f"{tarea.description}\n\n"
                    "Responde directamente con la información solicitada basándote "
                    "en tu conocimiento y en cualquier dato que hayas podido obtener."
                )
                return _llm_direct.invoke(prompt_fallback).content
            except Exception as e2:
                return f"Error en modo de compatibilidad: {e2}"
        raise


def main():
    inicializar_db()
    mem = cargar_memoria()

    print("\n🤖 [SISTEMA] Secuencia de inicio completada.")
    print(f"   📦 Flatpaks en memoria: {len(mem['flatpaks'])}")
    print(f"   💬 Turnos conversacionales recordados: {len(mem['conversacion'])}")

    modo   = "ACTIVO (ejecución autónoma)" if MODO_AUTONOMO else "MANUAL (requiere confirmación)"
    nombre = mem["preferencias"].get("nombre_usuario")
    print(f"🎙️  Javier: Buenos días, {nombre}. Matrices listas. Modo Autónomo: {modo}.\n")

    while True:
        try:
            orden = input("🧠 Creador: ").strip()
            if not orden:
                continue
            if orden.lower() in {"salir", "adios", "exit", "apágate", "quit"}:
                print("\n🤖 [SISTEMA] Desconectando sistemas. Hasta luego.")
                break

            # ── Comandos especiales de memoria ───────────────────────────────
            if _procesar_comando_memoria(orden, mem):
                print(f"\n{'─'*50}")
                continue

            # ── Atajo: visión de pantalla ────────────────────────────────────
            _PALABRAS_VISION = {"mira", "observa", "captura", "screenshot",
                                "pantalla", "ves", "qué contenido hay",
                                "que contenido hay", "qué ves", "que ves"}
            if any(x in orden.lower() for x in _PALABRAS_VISION):
                print("\n👁️  [Javier ACTIVANDO VISIÓN — Mueve el cursor al monitor deseado]")
                for _i in range(5, 0, -1):
                    print(f"   ⏳ {_i}...", end="\r", flush=True)
                    time.sleep(1)
                print("   📸 Capturando...                ")
                pregunta    = orden if len(orden) > 10 else "¿Qué ves en esta pantalla? Descríbela en detalle."
                descripcion = ver_pantalla(pregunta)
                print(f"\n🎙️  Javier: {descripcion}")
                registrar_turno(mem, "usuario", orden)
                registrar_turno(mem, "jarvis",  descripcion)
                print(f"\n{'─'*50}")
                continue

            # ── Atajo: flatpak conocido → lanzar directo sin LLM ────────────
            es_orden_lanzar = any(x in orden.lower() for x in
                                  ["ejecuta", "abre", "lanza", "inicia", "corre"])
            if es_orden_lanzar:
                app_id = buscar_flatpak_en_memoria(mem, orden)
                if app_id:
                    print(f"\n🚀 [MEMORIA]: {app_id} conocido. Lanzando directamente...")
                    salida, _ = ejecutar_comando(f"flatpak run {app_id}")
                    print(salida)
                    registrar_comando(mem, orden, f"flatpak run {app_id}")
                    registrar_turno(mem, "usuario", orden)
                    registrar_turno(mem, "jarvis",  f"Lanzado {app_id} directamente desde memoria.")
                    print(f"\n{'─'*50}")
                    continue

            # ── Flujo normal: agente LLM ─────────────────────────────────────
            registrar_turno(mem, "usuario", orden)
            usa_tools = false
            agente   = construir_agente(mem, con_tools=usa_tools)
            tarea    = _crear_tarea(orden, agente)

            print("\n🤖 [Javier PROCESANDO...]")
            respuesta = _ejecutar_crew(agente, tarea)

            comando = extraer_comando_shell(respuesta)
            if comando:
                respuesta_limpia = re.sub(
                    r"\[SHELL\].*?\[/SHELL\]|```[\w]*\n.*?\n```",
                    "", respuesta, flags=re.DOTALL
                ).strip()
                if respuesta_limpia:
                    print(f"\n🎙️  Javier: {respuesta_limpia}")

                print(f"\n⚠️  [SHELL DETECTADO]:")
                print(f"   \033[1;33m{comando}\033[0m")

                ejecutar = False
                if MODO_AUTONOMO:
                    print("⚡ [AUTÓNOMO]: Ejecutando directamente...")
                    ejecutar = True
                else:
                    conf    = input("¿Autorizar ejecución? [S/n]: ").strip().lower()
                    ejecutar = conf in ("", "s", "si", "y", "yes")

                if ejecutar:
                    print("\n⚙️  [EJECUTANDO EN ZSH...]")
                    salida, hubo_error = ejecutar_comando(comando)
                    print(f"\n{'─'*50}")
                    print(salida)
                    print(f"{'─'*50}")
                    registrar_comando(mem, orden, comando)

                    if "flatpak list" in comando and not hubo_error:
                        _intentar_lanzar_flatpak(mem, salida, orden)

                    print("\n🤖 [ANALIZANDO RESULTADO...]")
                    analisis = analizar_salida(orden, comando, salida)
                    print(f"\n🎙️  Javier: {analisis}")
                    registrar_turno(mem, "jarvis", analisis)
                else:
                    print("\n❌ [SISTEMA]: Ejecución denegada de forma segura.")

            elif _contiene_escritura(orden):
                nombre_arch, ok = escribir_archivo(orden, respuesta)
                if ok:
                    print(f"\n⚙️  [SISTEMA]: Archivo '{nombre_arch}' guardado.")
                    print(f"\n🎙️  Javier: Informe plasmado en '{nombre_arch}'.")
                else:
                    print(f"\n❌ [SISTEMA]: No pude escribir el archivo '{nombre_arch}'.")
                registrar_turno(mem, "jarvis", respuesta)

            else:
                print(f"\n🎙️  Javier: {respuesta}")
                registrar_turno(mem, "jarvis", respuesta)

            print(f"\n{'─'*50}")

        except KeyboardInterrupt:
            print("\n\n🤖 [SISTEMA] Apagado limpio. Hasta luego.")
            break
        except Exception as e:
            print(f"\n❌ [ERROR CRÍTICO]: {e}")


if __name__ == "__main__":
    main()