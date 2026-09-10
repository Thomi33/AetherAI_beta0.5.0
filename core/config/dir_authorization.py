"""
Autorización de directorio de trabajo.

Aether puede operar sobre archivos reales del sistema (fs_write, shell,
codigo, launch...), así que antes de tocar nada en una carpeta nueva le
pide autorización explícita al Creador -- UNA VEZ por carpeta, no en cada
turno. El "directorio de trabajo" es desde dónde se INVOCÓ `aether` (ver
bin/aether, que exporta $AETHER_CWD con el $PWD de invocación antes de
hacer `cd` al proyecto), no la carpeta del propio proyecto Aether.

Piezas:
- resolver_dir_trabajo(workdir_cli=None): de dónde se lanzó Aether.
  Prioridad: workdir_cli explícito (ej. `--workdir` ya parseado) > $AETHER_CWD
  (la exporta bin/aether, o la setean run.py/jarvis_new.py por sys.argv) > cwd
  real del proceso.
- esta_autorizado(ruta) / fue_evaluada(ruta): estado persistido.
- autorizar(ruta): persiste la decisión del Creador.
- denegar(ruta): no persiste la negativa, para volver a preguntar en el
  siguiente lanzamiento.
- listar_dirs(): rutas ya autorizadas (para `aether doctor`).
- presentacion_y_confirmacion(ruta, confirmar_fn=None): flujo completo
  para entrypoints de TERMINAL (jarvis_new.py, aether_run.py task) --
  imprime la presentación + pide confirmación por input() por defecto.
  La TUI (tui/app.py) usa su propio modal (DirAuthScreen en
  tui/widgets/selector_screens.py) y llama a autorizar()/denegar() por
  debajo, con el mismo texto de presentación (PRESENTACION) para que el
  mensaje sea consistente en los dos casos.

El allowlist vive en ~/.aether/allowed_dirs.json -- FUERA del repo,
porque es una decisión del USUARIO sobre SU sistema de archivos, no
configuración versionable del agente (a diferencia de skills/, que sí
vive dentro del repo). Forma:

    {"<ruta_absoluta>": {"autorizado": bool, "fecha": "<iso 8601 UTC>"}}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

RUTA_ALLOWLIST = Path.home() / ".aether" / "allowed_dirs.json"

PRESENTACION = """\
🤖 Soy Aether, tu agente de IA local (LangGraph + Ollama).

Todavía estoy en desarrollo activo: puedo ejecutar comandos de shell,
crear/leer/modificar/borrar archivos, lanzar programas y navegar la web
de forma autónoma. Eso también significa que puedo cometer errores --
revisá lo que hago, sobre todo al principio.

No trabajé nunca en esta carpeta:
  {ruta}
"""

PREGUNTA_CONFIRMACION = "¿Autorizás que Aether trabaje en esta carpeta? [s/N]: "


def resolver_dir_trabajo(workdir_cli: str | None = None) -> Path:
    """
    Resuelve el directorio de trabajo actual, en orden de prioridad:
      1. `workdir_cli` si se pasa explícito (ej. un --workdir ya parseado
         por el caller, sin pasar por la env var).
      2. $AETHER_CWD si está seteada (bin/aether la exporta con el $PWD
         de invocación, o run.py/jarvis_new.py con --workdir).
      3. El cwd real del proceso (fallback para cuando se corre algo
         directo sin pasar por bin/aether, ej. tests).
    """
    if workdir_cli and workdir_cli.strip():
        return Path(workdir_cli).expanduser().resolve()
    cwd_env = os.environ.get("AETHER_CWD")
    if cwd_env and cwd_env.strip():
        return Path(cwd_env).expanduser().resolve()
    return Path.cwd().resolve()


def _leer_allowlist() -> dict:
    if not RUTA_ALLOWLIST.is_file():
        return {}
    try:
        data = json.loads(RUTA_ALLOWLIST.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # Compatibilidad: la version anterior guardaba una LISTA de rutas que el
    # Creador ya usaba. Se migra a la forma nueva en memoria (autorizado=True,
    # fue una decision explicita suya en su momento); el archivo se reescribe
    # en formato nuevo la proxima vez que se persista una decision.
    if isinstance(data, list):
        data = {
            str(r): {"autorizado": True, "fecha": None, "migrado": True}
            for r in data
            if isinstance(r, str) and r.strip()
        }
    return data if isinstance(data, dict) else {}


def _escribir_allowlist(data: dict) -> None:
    RUTA_ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
    RUTA_ALLOWLIST.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def fue_evaluada(ruta: Path | str) -> bool:
    """True si ya se le preguntó al Creador por esta ruta EXACTA (sea cual haya sido la respuesta)."""
    return str(Path(ruta).expanduser().resolve()) in _leer_allowlist()


def esta_autorizado(ruta: Path | str) -> bool:
    """True solo si esa ruta EXACTA fue autorizada explícitamente (no hereda de un directorio padre)."""
    entrada = _leer_allowlist().get(str(Path(ruta).expanduser().resolve()))
    return bool(entrada and entrada.get("autorizado") is True)


def listar_dirs() -> list[str]:
    """Rutas ya AUTORIZADAS (no incluye las denegadas), ordenadas -- para `aether doctor`."""
    data = _leer_allowlist()
    return sorted(r for r, v in data.items() if isinstance(v, dict) and v.get("autorizado") is True)


def autorizar(ruta: Path | str) -> None:
    _guardar_decision(ruta, True)


def denegar(ruta: Path | str) -> None:
    ruta_abs = str(Path(ruta).expanduser().resolve())
    data = _leer_allowlist()
    if ruta_abs in data:
        del data[ruta_abs]
        _escribir_allowlist(data)


def _guardar_decision(ruta: Path | str, autorizado: bool) -> None:
    ruta_abs = str(Path(ruta).expanduser().resolve())
    data = _leer_allowlist()
    data[ruta_abs] = {
        "autorizado": autorizado,
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _escribir_allowlist(data)


def presentacion_y_confirmacion(ruta: Path | str, confirmar_fn=None) -> bool:
    """
    Flujo de autorización para entrypoints de TERMINAL: imprime la
    presentación del agente + pide confirmación, persiste la decisión y
    la     retorna. Las denegaciones no se persisten, por lo que una nueva ejecución
    vuelve a preguntar por la carpeta.

    `confirmar_fn`: inyectable para no depender de input() real (tests,
    o un caller que ya tiene su propio prompt). Por defecto usa input().
    """
    ruta = Path(ruta).expanduser().resolve()
    print(PRESENTACION.format(ruta=ruta))

    if confirmar_fn is None:
        resp = input(PREGUNTA_CONFIRMACION).strip().lower()
        ok = resp in ("s", "si", "sí", "y", "yes")
    else:
        ok = bool(confirmar_fn(str(ruta)))

    if ok:
        autorizar(ruta)
        print(f"✅ Autorizado. Trabajando en: {ruta}\n")
    else:
        denegar(ruta)
        print(
            f"⚠️  No autorizado. Aether se va a cerrar y no trabajará en esta "
            f"carpeta. Podés cambiarlo después editando {RUTA_ALLOWLIST}.\n"
        )
    return ok
