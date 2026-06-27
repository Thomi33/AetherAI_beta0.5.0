"""
Importador de datos LEGACY → current.db.

Lee la base vieja (memoria.db, esquema antiguo conversaciones/comandos/recuerdos)
y vuelca los datos al esquema nuevo a través de la write-API (MemoryStore), de
modo que TODO pasa por el guard de integridad. No modifica la DB legacy.

Mapeo:
    conversaciones(rol, texto, fecha)      → conversations(source, content, timestamp)
    comandos(orden, cmd, fecha)            → commands(command=cmd, result='', timestamp)
    recuerdos(categoria, contenido, imp)   → memories(type, content, importance, created_at)
        categoria 'preferencias' → type 'preference'; resto → 'fact'.

Las fechas legacy son ISO ('2026-06-25T11:08:..') o '%Y-%m-%d %H:%M'; se
convierten a epoch (int). Si no parsea, se usa ahora().
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

from core.memory.store import paths
from core.memory.store.store import MemoryStore


def _a_epoch(fecha: str | None) -> int:
    if not fecha:
        return int(time.time())
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(fecha, fmt).timestamp())
        except (ValueError, TypeError):
            continue
    # ISO genérico
    try:
        return int(datetime.fromisoformat(fecha).timestamp())
    except (ValueError, TypeError):
        return int(time.time())


def _tipo_desde_categoria(categoria: str | None) -> str:
    if categoria and categoria.strip().lower() in ("preferencias", "preference", "preferencia"):
        return "preference"
    return "fact"


def importar_legacy(
    db_legacy: Path | str | None = None,
    store: MemoryStore | None = None,
) -> dict[str, int]:
    """
    Importa la DB legacy al store (current.db por defecto). Devuelve un dict
    con cuántas filas se importaron por tabla. Idempotente a nivel de dedupe
    del store (re-ejecutar no duplica memorias; conversaciones/comandos podrían
    re-insertarse solo si no son consecutivos idénticos).
    """
    db_legacy = Path(db_legacy) if db_legacy else paths.DB_LEGACY
    if store is None:
        # Importador: sin snapshots automáticos (tomamos uno explícito al final).
        store = MemoryStore(paths.DB_CURRENT, snapshot_interval=0)

    resultado = {"conversations": 0, "commands": 0, "memories": 0}
    if not Path(db_legacy).exists():
        return resultado

    con = sqlite3.connect(f"file:{Path(db_legacy).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        def _tablas() -> set[str]:
            return {
                r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        tablas = _tablas()

        if "conversaciones" in tablas:
            for r in con.execute("SELECT rol, texto, fecha FROM conversaciones ORDER BY id ASC"):
                try:
                    if store.add_conversation(
                        content=r["texto"], source=r["rol"], timestamp=_a_epoch(r["fecha"]),
                    ):
                        resultado["conversations"] += 1
                except Exception:
                    pass

        if "comandos" in tablas:
            for r in con.execute("SELECT orden, cmd, fecha FROM comandos ORDER BY id ASC"):
                try:
                    if store.add_command(
                        command=r["cmd"], result="", timestamp=_a_epoch(r["fecha"]),
                    ):
                        resultado["commands"] += 1
                except Exception:
                    pass

        if "recuerdos" in tablas:
            for r in con.execute(
                "SELECT categoria, contenido, importancia, fecha FROM recuerdos ORDER BY id ASC"
            ):
                try:
                    if store.add_memory(
                        content=r["contenido"] or "",
                        type=_tipo_desde_categoria(r["categoria"]),
                        importance=r["importancia"] or 0,
                        created_at=_a_epoch(r["fecha"]),
                    ):
                        resultado["memories"] += 1
                except Exception:
                    pass
    finally:
        con.close()

    return resultado
