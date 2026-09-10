#!/usr/bin/env python3
"""
Aether Runtime CLI — tareas puntuales + diagnóstico (sin levantar la TUI).

Se usa a través del lanzador global `aether` (ver bin/aether) pero también
funciona directo. Resuelve las rutas del proyecto de forma relativa a su
propia ubicación, por lo que corre desde CUALQUIER directorio:

    python bin/aether_run.py version
    python bin/aether_run.py task "abre Firefox"            # usa el $PWD como dir. de trabajo
    python bin/aether_run.py task --workdir ~/Proyecto "..."  # opera en otra carpeta
    python bin/aether_run.py doctor

Desde el lanzador global, el directorio de trabajo se captura automáticamente
($PWD de invocación → $AETHER_CWD → settings.RUTA_TRABAJO); `--workdir` lo pisa.

Comandos:
    version   imprime versión del runtime + revisión git
    task      ejecuta una orden contra el grafo y devuelve la respuesta final
    doctor    verifica python/venv/ollama/config/db y reporta estado
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

AETHER_VERSION = "1.0.0"


# ─────────────────────────────────────────────────────────────────────────
# version
# ─────────────────────────────────────────────────────────────────────────
def cmd_version() -> int:
    rev = ""
    try:
        out = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        rev = out.stdout.strip()
    except Exception:
        rev = ""
    suffix = f"  /  git {rev}" if rev else ""
    print(f"Aether runtime v{AETHER_VERSION}  [python {sys.version.split()[0]}]{suffix}")
    return 0


# ─────────────────────────────────────────────────────────────────────────
# task (one-shot sobre el grafo)
# ─────────────────────────────────────────────────────────────────────────
def cmd_task(mensaje: str, workdir: str | None = None) -> int:
    if not mensaje:
        print('❌ `aether task` necesita una orden. Ej: aether task "abre Firefox"',
              file=sys.stderr)
        return 2

    # Fijar dir. de trabajo ANTES de importar el grafo (settings es lazy
    # pero dir_authorization pide confirmación acá en TTY).
    import os as _os
    if workdir:
        _os.environ["AETHER_CWD"] = workdir
    from core.config.dir_authorization import (
        esta_autorizado,
        resolver_dir_trabajo,
        presentacion_y_confirmacion,
    )
    from core.config import settings as _s
    ruta = resolver_dir_trabajo(workdir_cli=workdir)
    if not esta_autorizado(ruta):
        if not presentacion_y_confirmacion(ruta):
            print("Aether se cierra porque el directorio no fue autorizado.", file=sys.stderr)
            return 1
    print(f"📁 Dir. trabajo: {ruta}")
    print(f"🧠 Creador: {mensaje}")
    try:
        from core.memory.memory_manager import cargar_memoria
        from core.services.graph_service import procesar_orden_grafo

        mem = cargar_memoria()
        respuesta = procesar_orden_grafo(mensaje, mem, modo_autonomo=True)
        print(f"🎙️  Aether: {respuesta}")
        return 0
    except KeyboardInterrupt:
        print("\n🤖 Interrumpido.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — reporte legible al usuario
        print(f"❌ Error al procesar la orden: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


# ─────────────────────────────────────────────────────────────────────────
# doctor — diagnóstico del entorno
# ─────────────────────────────────────────────────────────────────────────
def _marca(ok: bool, label: str, detalle: str = "") -> str:
    icono = "✅" if ok else "❌"
    sufijo = f"  ({detalle})" if detalle else ""
    print(f"  {icono} {label}{sufijo}")
    return ok


def cmd_doctor() -> int:
    ok = True
    print(f"🔍 Doctor de Aether — {PROJECT_ROOT}")
    print(f"   python: {sys.executable}  ({sys.version.split()[0]})")

    # 1. Dependencias críticas
    for pkg, mod in (("textual", "textual"), ("rich", "rich"),
                     ("langgraph", "langgraph"), ("ollama", "ollama")):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            ok = _marca(True, f"{pkg} instalado", str(ver)) and ok
        except Exception as exc:
            ok = _marca(False, f"{pkg} instalado", str(exc)) and ok

    # 2. Config y datos
    config_path = PROJECT_ROOT / "core" / "config" / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            ok = _marca(True, "config.json legible",
                        f"MODELO={cfg.get('MODELO')!r}") and ok
        except Exception as exc:
            ok = _marca(False, "config.json legible", str(exc)) and ok
    else:
        ok = _marca(False, "config.json existe", str(config_path)) and ok

    try:
        from core.config import settings as _s
        base = Path(_s.BASE_AETHER).expanduser()
        ok = _marca(base.exists(), "BASE_AETHER existe", str(base)) and ok
        try:
            from core.config.dir_authorization import listar_dirs, esta_autorizado
            from core.config import settings as _st
            _rt = _st.RUTA_TRABAJO
            ok = _marca(_rt.is_dir(), "dir. trabajo actual", f"{_rt} ({'autorizado' if esta_autorizado(_rt) else 'nuevo'})") and ok
            _dirs = listar_dirs()
            ok = _marca(True, "dirs autorizados", f"{len(_dirs)}: {', '.join(_dirs[:4])}{'...' if len(_dirs) > 4 else ''}" if _dirs else "ninguno aún") and ok
        except Exception as exc2:
            ok = _marca(False, "dirs autorizados", str(exc2)) and ok
    except Exception as exc:
        ok = _marca(False, "core.config importable", str(exc)) and ok

    # 3. Ollama
    ollama = shutil.which("ollama")
    if not ollama:
        ok = _marca(False, "ollama en el PATH") and ok
    else:
        ok = _marca(True, "ollama en el PATH", ollama) and ok
        try:
            out = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0:
                modelos = [l for l in out.stdout.strip().splitlines()[1:] if l.strip()]
                ok = _marca(True, "ollama responde", f"{len(modelos)} modelos") and ok
            else:
                ok = _marca(False, "ollama list", (out.stderr or "").strip()[:120]) and ok
        except Exception as exc:
            ok = _marca(False, "ollama list", str(exc)) and ok

    # 4. DB de producción (correcta: current.db del nuevo subsistema)
    try:
        from core.config import settings as _s
        ruta_db = Path(_s.DB_CURRENT).expanduser()
        estado = "ok" if ruta_db.exists() else "no existe"
        ok = _marca(ruta_db.exists(), "DB de producción", f"{ruta_db} ({estado})") and ok
    except Exception as exc:
        ok = _marca(False, "DB de producción detectada", str(exc)) and ok

    print("")
    print("✅ Entorno listo." if ok else "❌ Hay problemas que corregir (ver arriba).")
    return 0 if ok else 1


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aether",
        description="Runtime CLI de Aether (agente local LangGraph + Ollama).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_version = sub.add_parser("version", help="versión del runtime + revisión git")
    p_version.set_defaults(func=lambda _: cmd_version())

    p_task = sub.add_parser("task", help="ejecuta una orden one-shot sobre el grafo")
    p_task.add_argument("orden", nargs="+", help="la orden a ejecutar (ej: abre Firefox)")
    p_task.add_argument("--workdir", default=None, help="dir. de trabajo (default: desde donde se invoca)")
    p_task.set_defaults(func=lambda a: cmd_task(" ".join(a.orden), workdir=a.workdir))

    p_doctor = sub.add_parser("doctor", help="diagnóstico del entorno")
    p_doctor.set_defaults(func=lambda _: cmd_doctor())

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())