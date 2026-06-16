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
import shutil
import sqlite3

# =====================================================================
# ⚙️  CONFIGURACIÓN GLOBAL
# =====================================================================
MODO_AUTONOMO   = True
OLLAMA_HOST     = "http://localhost:11434"
SEARXNG_URL     = "http://localhost:8081"
MODELO          = "gemma4:12b"
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
        "programas":          {},
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
    #print(f"DEBUG SQLITE: Guardando turno en DB | Rol: {rol} | Texto: {texto[:50]}...")


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
    #print(f"DEBUG SQLITE CMD: {cmd}")


def registrar_comando(mem, orden, cmd):
    registrar_comando_db(orden, cmd)
    entrada = {"orden": orden, "cmd": cmd, "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")}
    mem["historial_comandos"].append(entrada)
    mem["historial_comandos"] = mem["historial_comandos"][-50:]


def actualizar_flatpaks(mem: dict, salida_lista: str) -> None:
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


def actualizar_programas(mem: dict):
    mem["programas"] = {}

    # Segmentos de ruta a excluir: entornos virtuales Python
    _EXCLUIR = {"env", "venv", ".venv", "__pycache__", "site-packages"}

    for ruta in os.environ.get("PATH", "").split(":"):
        if not os.path.isdir(ruta):
            continue

        # Excluir rutas que contienen segmentos de entornos virtuales
        if any(seg in Path(ruta).parts for seg in _EXCLUIR):
            continue

        try:
            for archivo in os.listdir(ruta):
                ejecutable = os.path.join(ruta, archivo)

                if (
                    os.path.isfile(ejecutable)
                    and os.access(ejecutable, os.X_OK)
                    # Ignorar nombres muy cortos: propensos a falsos positivos
                    and len(archivo) >= 3
                ):
                    mem["programas"][archivo.lower()] = ejecutable

        except PermissionError:
            continue


def buscar_programa_en_memoria(mem: dict, orden: str):
    """
    Busca un programa por nombre EXACTO de palabra (no substring).
    Evita falsos positivos como 'ec' dentro de 'hecho'.
    """
    import re as _re
    orden_lower = orden.lower()

    for nombre, ruta in mem["programas"].items():
        # Coincidencia de palabra completa usando 
        patron = r"" + _re.escape(nombre) + r""
        if _re.search(patron, orden_lower):
            return ruta

        # Variante con guiones como espacios (ej: "brave-browser" -> "brave browser")
        nombre_espacios = nombre.replace("-", " ")
        if len(nombre_espacios) >= 3:
            patron2 = r"" + _re.escape(nombre_espacios) + r""
            if _re.search(patron2, orden_lower):
                return ruta

    return None


def buscar_programa_sistema(nombre: str):
    return shutil.which(nombre)


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


def _buscar_web_impl(query: str) -> str:
    """Implementación real de búsqueda web (callable directamente sin CrewAI)."""
    print(f"\n🔍 [AETHER ESTA BUSCANDO]: '{query}'...")
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
            titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text)[:5]
            links  = re.findall(r'class="result__url"[^>]*href="([^"]+)"', r.text)[:5]
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


def _leer_url_impl(url: str) -> str:
    """Implementación real de lectura de URL (callable directamente sin CrewAI)."""
    url = _desempaquetar_ddg(url)
    print(f"\n📖 [AETHER ESTA NAVEGANDO]: {url}")
    try:
        r = requests.get(url, headers=_HEADERS_WEB, timeout=12)
        if r.status_code == 200:
            texto = _limpiar_html(r.text)[:7000]
            print(f"⚡ Extraídos {len(texto):,} caracteres limpios.")
            return f"[CONTENIDO DE {url}]\n{texto}"
        return f"HTTP {r.status_code} al intentar leer la página."
    except Exception as e:
        return f"Error al navegar la URL: {e}"


# Wrappers @tool conservados por compatibilidad (no se usan en el flujo principal)
@tool("Buscar en la Web con SearXNG")
def buscar_web(query: str) -> str:
    """Busca en internet y devuelve títulos, URLs y resúmenes de los resultados."""
    return _buscar_web_impl(query)

@tool("Leer Contenido de una URL")
def leer_url(url: str) -> str:
    """Accede a una URL y extrae el texto principal del artículo o página."""
    return _leer_url_impl(url)

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
        "think": True,
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


# =====================================================================
# 🧠 ROUTER INTELIGENTE — reemplaza CrewAI ReAct (incompatible con Gemma4)
# =====================================================================

_KW_BUSQUEDA = frozenset([
    "busca", "buscar", "googlea", "internet", "web", "versión", "version",
    "última", "ultimo", "noticias", "noticia", "investiga", "qué es", "que es",
    "cómo funciona", "como funciona", "precio", "release", "descargar", "descarga",
    "existe", "hay algún", "hay algun", "cuál es la", "cual es la",
])

_KW_SISTEMA = frozenset([
    "ejecuta", "corre", "instala", "desinstala", "actualiza", "pacman", "yay",
    "flatpak", "systemctl", "comando", "temperatura", "uso de disco", "espacio",
    "memoria ram", "cpu", "procesos", "listar", "lista los", "muéstrame los",
    "muestra los", "ver logs", "check", "verifica", "sensor",
])


def _clasificar_orden(orden: str) -> str:
    """Clasifica la orden sin llamar al LLM: 'conversacion' | 'busqueda' | 'sistema' | 'busqueda_sistema'"""
    o = orden.lower()
    web = any(k in o for k in _KW_BUSQUEDA)
    sys = any(k in o for k in _KW_SISTEMA)
    if web and sys:
        return "busqueda_sistema"
    if web:
        return "busqueda"
    if sys:
        return "sistema"
    return "conversacion"


def _system_prompt(mem: dict) -> str:
    ctx = construir_contexto_memoria(mem)
    return f"""Eres Aether, un asistente de IA avanzado y leal. Tratas al Creador como tu mejor amigo. Eres preciso, directo y sin evasivas.

SISTEMA OPERATIVO: Arch Linux con zsh. NUNCA uses apt, apt-get, dnf, yum o snap.
Gestor de paquetes: pacman. AUR: yay. Apps gráficas: flatpak.

{ctx}

PROTOCOLO FLATPAK: Si el Creador pide abrir una app Flatpak no conocida, lista primero:
  [SHELL] flatpak list --columns=application,name [/SHELL]
Luego lanza: [SHELL] flatpak run <ID_EXACTO> [/SHELL]

REGLAS DE FORMATO — CRÍTICO:
- NUNCA uses bloques Markdown (``` ni ~~~).
- NUNCA uses encabezados (###, ##, #).
- NUNCA uses negrita (**texto**) ni cursiva (*texto*).
- SOLO texto plano y natural.
- Comandos de sistema ÚNICAMENTE entre [SHELL] y [/SHELL].
- Llama al Creador "Socio" o "Cara"."""


def _llm_invoke(prompt: str, mem: dict) -> str:
    """Llama directamente al LLM con system prompt y contexto de memoria."""
    from langchain_core.messages import SystemMessage, HumanMessage
    try:
        msgs = [
            SystemMessage(content=_system_prompt(mem)),
            HumanMessage(content=prompt),
        ]
        return _llm_direct.invoke(msgs).content
    except Exception as e:
        return f"Error al invocar el modelo: {e}"


def _procesar_orden(orden: str, mem: dict) -> str:
    """
    Router principal: clasifica la orden y la procesa SIN CrewAI.
    Gemma4 no soporta el formato ReAct de CrewAI — este router lo reemplaza
    ejecutando herramientas directamente y pasando los resultados al LLM.
    """
    tipo = _clasificar_orden(orden)

    if tipo == "conversacion":
        # Llamada directa: sin herramientas, sin ReAct
        return _llm_invoke(orden, mem)

    elif tipo == "busqueda":
        # 1. Buscar
        resultados = _buscar_web_impl(orden)
        # 2. Leer primera URL relevante (evitar Reddit, YouTube, Twitter)
        urls = re.findall(r'URL:\s*(https?://[^\s\n]+)', resultados)
        contenido_extra = ""
        for url in urls[:2]:
            url = url.rstrip(".,)")
            if not any(x in url for x in ["reddit.com", "youtube.com", "twitter.com", "x.com"]):
                contenido_extra = _leer_url_impl(url)
                if len(contenido_extra) > 200:
                    break
        # 3. LLM sintetiza con los datos reales
        contexto = f"RESULTADOS DE BÚSQUEDA WEB:\n{resultados}"
        if contenido_extra:
            contexto += f"\n\nCONTENIDO DETALLADO:\n{contenido_extra[:4000]}"
        prompt = (
            f"{orden}\n\n{contexto}\n\n"
            "Basándote ÚNICAMENTE en estos resultados reales, responde de forma directa y asertiva. "
            "Si hay un comando de sistema relevante, inclúyelo con [SHELL] comando [/SHELL]."
        )
        return _llm_invoke(prompt, mem)

    elif tipo == "sistema":
        # LLM genera el comando directamente
        prompt = (
            f"{orden}\n\n"
            "Propón el comando exacto para Arch Linux/zsh usando [SHELL] comando [/SHELL]. "
            "Explica brevemente qué hace y qué esperar del resultado."
        )
        return _llm_invoke(prompt, mem)

    elif tipo == "busqueda_sistema":
        # Buscar primero, luego generar comando con contexto real
        resultados = _buscar_web_impl(orden)
        prompt = (
            f"{orden}\n\nRESULTADOS DE BÚSQUEDA:\n{resultados}\n\n"
            "Basándote en estos resultados, propón el comando exacto para Arch Linux "
            "usando [SHELL] comando [/SHELL]."
        )
        return _llm_invoke(prompt, mem)

    # Fallback
    return _llm_invoke(orden, mem)

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
    r"|brave|electron|appimage|prismlauncher"
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
            print(f"\n🚀 [Aether]: ID encontrado → {app_id}. Lanzando ahora...")
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
        f'La orden fue: "{orden}". Comando ejecutado: `{comando}`.\n'
        f'Resultado:\n"""\n{salida[:3000]}\n"""\n\n'
        "Escribe un análisis claro y directo en TEXTO PLANO. "
        "SIN bloques de código, SIN Markdown, SIN ejemplos de error. "
        "Solo prosa breve: qué pasó y qué esperar."
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
            "think":  True,
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
    """
    Extrae comandos SOLO del formato [SHELL]...[/SHELL].
    NO extrae de bloques Markdown — el LLM los usa para ejemplos
    que NO deben ejecutarse (ej: 'NameError:...' en texto explicativo).
    """
    m = re.search(r"\[SHELL\]\s*(.*?)\s*\[/SHELL\]", texto, re.DOTALL)
    if m:
        cmd = m.group(1).strip()
        # Sanity check: rechazar si parece un mensaje de error Python, no un comando
        if cmd and not re.match(r"^[A-Z][a-zA-Z]+Error:", cmd):
            return cmd
    return None

def extraer_nombre_app(orden: str):
    palabras = orden.lower().split()

    ignorar = {
        "abre", "ejecuta", "lanza",
        "inicia", "corre", "por",
        "favor", "el", "la", "los",
        "las", "programa"
    }

    candidatas = [p for p in palabras if p not in ignorar]

    return candidatas[-1] if candidatas else None

def buscar_programa_sistema(nombre: str) -> str | None:
    ejecutable = shutil.which(nombre)
    if ejecutable:
        return ejecutable
    return None

# =====================================================================
# 🔧 COMANDOS ESPECIALES DE MEMORIA
# =====================================================================

def _procesar_comando_memoria(orden: str, mem: dict) -> bool:
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

# =====================================================================
# 🎙️  BUCLE PRINCIPAL
# =====================================================================

PALABRAS_CLAVE_ESCRITURA = frozenset([
    "escribe", "crea", "guardar", "guarda", "crear",
    "archivo", "txt", "reporte", "documento", "informe", "json", "md",
])


def _contiene_escritura(orden: str) -> bool:
    return bool(set(orden.lower().split()) & PALABRAS_CLAVE_ESCRITURA)


# _crear_tarea y _ejecutar_crew eliminados — reemplazados por _procesar_orden()
# CrewAI ReAct es incompatible con Gemma4 (no sigue el formato Thought/Action/Final Answer)


def main():
    inicializar_db()
    mem = cargar_memoria()
    actualizar_programas(mem)

    print("\n🤖 [SISTEMA] Secuencia de inicio completada.")
    print(f"   📦 Flatpaks recordados: {len(mem['flatpaks'])}")
    print(f"   💬 Turnos conversacionales recordados: {len(mem['conversacion'])}")
    print(f"🖥️  Programas detectados: {len(mem['programas'])}")

    modo   = "ACTIVO (ejecución autónoma)" if MODO_AUTONOMO else "MANUAL (requiere confirmación)"
    nombre = mem["preferencias"].get("nombre_usuario")
    print(f"🎙️  Aether: Buenos días, {nombre}. Todo listo aqui en modo autónomo: {modo}.\n")

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
                                "en mi pantalla", "ves", "qué contenido hay",
                                "que contenido hay", "qué ves", "que ves"}
            if any(x in orden.lower() for x in _PALABRAS_VISION):
                print("\n👁️  [Aether ACTIVANDO VISIÓN — Mueve el cursor al monitor deseado]")
                for _i in range(5, 0, -1):
                    print(f"   ⏳ {_i}...", end="\r", flush=True)
                    time.sleep(1)
                print("   📸 Capturando...                ")
                pregunta    = orden if len(orden) > 10 else "¿Qué ves en esta pantalla? Descríbela en detalle."
                descripcion = ver_pantalla(pregunta)
                print(f"\n🎙️  Aether: {descripcion}")
                registrar_turno(mem, "usuario", orden)
                registrar_turno(mem, "jarvis",  descripcion)
                print(f"\n{'─'*50}")
                continue

            # ── Atajo: flatpak conocido → lanzar directo sin LLM ────────────
            es_orden_lanzar = any(
                x in orden.lower()
                for x in ["ejecuta", "abre", "lanza", "inicia", "corre"]
            )

            if es_orden_lanzar:

                # Flatpak conocido
                app_id = buscar_flatpak_en_memoria(mem, orden)

                if app_id:
                    print(f"\n🚀 [MEMORIA FLATPAK]: {app_id}")

                    salida, _ = ejecutar_comando(
                        f"flatpak run {app_id}"
                    )

                    print(salida)

                    registrar_comando(
                        mem,
                        orden,
                        f"flatpak run {app_id}"
                    )

                    registrar_turno(
                        mem,
                        "usuario",
                        orden
                    )

                    registrar_turno(
                        mem,
                        "jarvis",
                        f"Lanzado {app_id} desde memoria Flatpak."
                    )

                    print(f"\n{'─'*50}")
                    continue

                # Programa nativo conocido
                ruta = buscar_programa_en_memoria(
                    mem,
                    orden
                )

                if ruta:
                    print(f"\n🚀 [BINARIO]: {ruta}")

                    salida, _ = ejecutar_comando(ruta)

                    print(salida)

                    registrar_comando(
                        mem,
                        orden,
                        ruta
                    )

                    registrar_turno(
                        mem,
                        "usuario",
                        orden
                    )

                    registrar_turno(
                        mem,
                        "jarvis",
                        f"Lanzado {ruta} desde binarios."
                    )

                    print(f"\n{'─'*50}")
                    continue

                # Último intento
                nombre_app = extraer_nombre_app(
                    orden
                )

                if nombre_app:

                    ruta = buscar_programa_sistema(
                        nombre_app
                    )

                    if ruta:
                        print(f"\n🚀 [WHICH]: {ruta}")

                        salida, _ = ejecutar_comando(
                            ruta
                        )

                        print(salida)

                        registrar_comando(
                            mem,
                            orden,
                            ruta
                        )

                        registrar_turno(
                            mem,
                            "usuario",
                            orden
                        )

                        registrar_turno(
                            mem,
                            "jarvis",
                            f"Lanzado {ruta} mediante shutil.which()."
                        )

                        print(f"\n{'─'*50}")
                        continue


            # ── Flujo principal: router inteligente (sin CrewAI) ─────────────
            registrar_turno(mem, "usuario", orden)

            tipo = _clasificar_orden(orden)
            iconos = {
                "conversacion":    "💬",
                "busqueda":        "🌐",
                "sistema":         "⚙️ ",
                "busqueda_sistema": "🌐⚙️ ",
            }
            print(f"\n🤖 [Aether PROCESANDO... {iconos.get(tipo, '')} modo {tipo}]")
            respuesta = _procesar_orden(orden, mem)

            comando = extraer_comando_shell(respuesta)
            if comando:
                respuesta_limpia = re.sub(
                    r"\[SHELL\].*?\[/SHELL\]|```[\w]*\n.*?\n```",
                    "", respuesta, flags=re.DOTALL
                ).strip()
                if respuesta_limpia:
                    print(f"\n🎙️  Aether: {respuesta_limpia}")

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
                    print(f"\n🎙️  Aether: {analisis}")
                    registrar_turno(mem, "jarvis", analisis)
                else:
                    print("\n❌ [SISTEMA]: Ejecución denegada de forma segura.")

            elif _contiene_escritura(orden):
                nombre_arch, ok = escribir_archivo(orden, respuesta)
                if ok:
                    print(f"\n⚙️  [SISTEMA]: Archivo '{nombre_arch}' guardado.")
                    print(f"\n🎙️  Aether: Informe plasmado en '{nombre_arch}'.")
                else:
                    print(f"\n❌ [SISTEMA]: No pude escribir el archivo '{nombre_arch}'.")
                registrar_turno(mem, "jarvis", respuesta)

            else:
                print(f"\n🎙️  Aether: {respuesta}")
                registrar_turno(mem, "jarvis", respuesta)

            print(f"\n{'─'*50}")

        except KeyboardInterrupt:
            print("\n\n🤖 [SISTEMA] Apagado limpio. Hasta luego.")
            break
        except Exception as e:
            print(f"\n❌ [ERROR CRÍTICO]: {e}")


if __name__ == "__main__":
    main()