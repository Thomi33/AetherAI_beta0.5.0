"""
dir_authorization.py — Autorización explícita del directorio de trabajo.

Aether trabaja sobre la carpeta desde la que se lo invoca (~/Documents,
~/Proyecto, ...), NO encerrado en ~/Aether. Como eso implica escribir/
ejecutar fuera del sandbox original, cada directorio requiere autorización
explícita del usuario UNA sola vez; queda registrada en
~/.aether/allowed_dirs.json.

Prioridad de resolución (resolver_dir_trabajo):
  1. --workdir <ruta> explícito (siempre pide confirmación si es nuevo)
  2. $AETHER_CWD (lo exporta bin/aether con el $PWD original antes del cd)
  3. os.getcwd() (cuando se corre directo: python run.py)

Seguridad:
  - dentro_de(): jail blando — las tools verifican que el destino final
    esté dentro de RUTA_TRABAJO o sea ruta absoluta explícita del usuario.
  - Rutas del sistema (/etc, /sys, /proc, /dev, /boot, /root) siempre
    requieren confirmación aunque el dir padre esté autorizado.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ALLOWLIST_PATH = Path.home() / ".aether" / "allowed_dirs.json"

# Directorios donde NUNCA se opera sin confirmación explícita por comando,
# aunque el directorio de trabajo esté autorizado.
RUTAS_SENSIBLES = ("/etc", "/sys", "/proc", "/dev", "/boot", "/root",
                   "/usr/lib", "/usr/bin", "/bin", "/sbin")


def _cargar_allowlist() -> set[str]:
    try:
        data = json.loads(ALLOWLIST_PATH.read_text())
        return {str(Path(p).expanduser()).rstrip("/") for p in data if p}
    except Exception:
        return set()


def _guardar_allowlist(dirs: set[str]) -> None:
    try:
        ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALLOWLIST_PATH.write_text(json.dumps(sorted(dirs), indent=2))
    except Exception:
        pass


def esta_autorizado(ruta: str | Path) -> bool:
    """True si la ruta está dentro de algún directorio autorizado."""
    r = str(Path(ruta).expanduser().resolve())
    for base in _cargar_allowlist():
        try:
            if Path(r) == Path(base) or Path(r).is_relative_to(base):
                return True
        except Exception:
            if r == base or r.startswith(base.rstrip("/") + "/"):
                return True
    return False


def es_ruta_sensible(ruta: str | Path) -> bool:
    r = str(Path(ruta).expanduser()).rstrip("/")
    return any(r == s or r.startswith(s.rstrip("/") + "/")
               for s in RUTAS_SENSIBLES)


def dentro_de(ruta: str | Path, base: str | Path) -> bool:
    """Jail blando: True si `ruta` resuelve dentro de `base`."""
    try:
        return (Path(ruta).expanduser().resolve().is_relative_to(
            Path(base).expanduser().resolve()))
    except Exception:
        r = str(Path(ruta).expanduser().resolve())
        b = str(Path(base).expanduser().resolve()).rstrip("/")
        return r == b or r.startswith(b + "/")


def autorizar_dir(ruta: str | Path, no_confirm: bool = False,
                  preguntar_fn=None) -> bool:
    """Registra el dir como autorizado. Pide confirmación salvo no_confirm
    o que ya esté autorizado. Retorna True si quedó autorizado."""
    r = str(Path(ruta).expanduser().resolve())
    if esta_autorizado(r):
        return True
    if not no_confirm:
        if preguntar_fn is None:
            try:
                resp = input(f"📁 ¿Autorizar a Aether a trabajar en {r}? [S/n] ")
            except EOFError:
                resp = "n"
        else:
            resp = preguntar_fn(r)
        if str(resp).strip().lower() not in ("", "s", "si", "sí", "y", "yes"):
            return False
    dirs = _cargar_allowlist()
    dirs.add(r)
    _guardar_allowlist(dirs)
    return True


def resolver_dir_trabajo(workdir_cli: str | None = None,
                         no_confirm: bool = False) -> Path:
    """Resuelve y autoriza el directorio de trabajo.

    Orden: --workdir > $AETHER_CWD (bin/aether) > cwd actual.
    Si el dir es nuevo y hay TTY, pide confirmación una sola vez.
    Sin TTY (pipes/scripts) y dir nuevo: autoriza igual pero lo deja
    registrado (el usuario lo ve en el banner de sesión).
    """
    candidato = (workdir_cli
                 or os.environ.get("AETHER_CWD")
                 or os.getcwd())
    ruta = Path(candidato).expanduser().resolve()
    if not ruta.is_dir():
        # Si no existe (typo en --workdir), caer al cwd real.
        ruta = Path(os.getcwd()).resolve()
    tiene_tty = False
    try:
        tiene_tty = bool(os.isatty(0))
    except Exception:
        pass
    if tiene_tty and not no_confirm:
        autorizar_dir(ruta, no_confirm=False)
    else:
        # No interactivo: registrar silenciosamente (visible en banner/doctor).
        dirs = _cargar_allowlist()
        if str(ruta) not in dirs:
            dirs.add(str(ruta))
            _guardar_allowlist(dirs)
    return ruta


def listar_dirs() -> list[str]:
    return sorted(_cargar_allowlist())
