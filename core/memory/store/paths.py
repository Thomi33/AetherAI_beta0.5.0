"""
Rutas del subsistema de memoria. Centraliza dónde viven las DBs y dirs.

- current.db  : ÚNICA base de producción (toda escritura va acá).
- staging.db  : pruebas antes de pasar a producción.
- snapshots/  : copias inmutables para rollback.
- backups/    : respaldos automáticos previos a un rollback/operación riesgosa.
- migrations/ : SQL versionado (vive en el repo, no en el dir de datos).
"""
from __future__ import annotations

from pathlib import Path

from core.config import settings

# Directorio de migraciones (en el repo, junto a este paquete).
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

DB_CURRENT: Path    = Path(settings.DB_CURRENT)
DB_STAGING: Path    = Path(settings.DB_STAGING)
SNAPSHOTS_DIR: Path = Path(settings.SNAPSHOTS_DIR)
BACKUPS_DIR: Path   = Path(settings.BACKUPS_DIR)

# DB legacy (esquema viejo) — solo para el importador de datos.
DB_LEGACY: Path     = Path(settings.RUTA_DB)


def asegurar_directorios() -> None:
    """Crea los directorios de datos si no existen (idempotente)."""
    DB_CURRENT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
