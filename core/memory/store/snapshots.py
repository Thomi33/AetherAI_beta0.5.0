"""
Snapshots inmutables y rollback del subsistema de memoria.

- Un snapshot es una copia CONSISTENTE de current.db (vía la backup API de
  SQLite, segura bajo WAL) guardada en SNAPSHOTS_DIR y marcada read-only
  (inmutable, best-effort a nivel filesystem).
- rollback() restaura un snapshot sobre current.db, pero ANTES respalda el
  current actual en BACKUPS_DIR → el rollback es a su vez reversible.
"""
from __future__ import annotations

import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

from core.memory.store import paths


def _ts() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S_%f")


def _sane(motivo: str) -> str:
    """Sanitiza el motivo para usarlo en el nombre de archivo."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (motivo or "snap"))[:40]


def _copia_consistente(origen: Path, destino: Path) -> None:
    """Copia origen→destino usando la backup API (consistente con WAL)."""
    src = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(destino))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def crear_snapshot(db_path: Path | None = None, motivo: str = "manual") -> Path | None:
    """
    Crea un snapshot inmutable de `db_path` (por defecto current.db).
    Devuelve la ruta del snapshot, o None si la DB origen no existe.
    """
    db_path = Path(db_path) if db_path else paths.DB_CURRENT
    if not db_path.exists():
        return None
    paths.asegurar_directorios()
    destino = paths.SNAPSHOTS_DIR / f"{_ts()}__{_sane(motivo)}.db"
    _copia_consistente(db_path, destino)
    # Inmutable best-effort: solo lectura.
    try:
        os.chmod(destino, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass
    return destino


def listar_snapshots() -> list[Path]:
    """Snapshots existentes, del MÁS RECIENTE al más viejo."""
    if not paths.SNAPSHOTS_DIR.exists():
        return []
    snaps = [p for p in paths.SNAPSHOTS_DIR.glob("*.db") if p.is_file()]
    return sorted(snaps, reverse=True)


def snapshot_mas_reciente() -> Path | None:
    snaps = listar_snapshots()
    return snaps[0] if snaps else None


def crear_backup(db_path: Path | None = None, motivo: str = "backup") -> Path | None:
    """
    Respaldo (NO inmutable) de `db_path` en BACKUPS_DIR. Se usa antes de un
    rollback para no perder el estado actual.
    """
    db_path = Path(db_path) if db_path else paths.DB_CURRENT
    if not db_path.exists():
        return None
    paths.asegurar_directorios()
    destino = paths.BACKUPS_DIR / f"{_ts()}__{_sane(motivo)}.db"
    _copia_consistente(db_path, destino)
    return destino


def rollback(snapshot_path: Path | str, db_path: Path | None = None) -> Path:
    """
    Restaura `snapshot_path` sobre current.db. Respalda el current actual en
    BACKUPS_DIR primero (rollback reversible). Devuelve la ruta del backup
    creado (o None si no había current previo).

    Levanta FileNotFoundError si el snapshot no existe.
    """
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Snapshot inexistente: {snapshot_path}")

    db_path = Path(db_path) if db_path else paths.DB_CURRENT
    paths.asegurar_directorios()

    backup_previo = crear_backup(db_path, motivo="pre_rollback")

    # Limpiar current + sidecars WAL/SHM para una restauración limpia.
    for sufijo in ("", "-wal", "-shm"):
        p = Path(str(db_path) + sufijo)
        if p.exists():
            try:
                os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            p.unlink()

    # Restaurar (copia consistente del snapshot → current, current queda RW).
    _copia_consistente(snapshot_path, db_path)
    try:
        os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass
    return backup_previo
