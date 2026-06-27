"""
Runner de migraciones versionadas.

- Las migraciones son archivos `NNNN_nombre.sql` en migrations/.
- Se aplican EN ORDEN, una sola vez, y se registran en la tabla
  `schema_migrations` (version, name, applied_at, checksum).
- Es EXPLÍCITO: lo invoca el arranque del sistema (inicializar_db) o una CLI;
  el agente NUNCA aplica migraciones por su cuenta.
- Antes de migrar una DB que YA tiene migraciones aplicadas (producción con
  datos), se crea un snapshot inmutable para permitir rollback.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from core.memory.store import connection as conn
from core.memory.store import paths
from core.memory.store.schema import MIGRATIONS_TABLE

_PATRON_MIGRACION = re.compile(r"^(\d+)_.*\.sql$")


def _migraciones_disponibles() -> list[tuple[int, Path]]:
    """Lista [(version, ruta)] de migrations/ ordenada por versión asc."""
    res: list[tuple[int, Path]] = []
    if not paths.MIGRATIONS_DIR.exists():
        return res
    for p in paths.MIGRATIONS_DIR.glob("*.sql"):
        m = _PATRON_MIGRACION.match(p.name)
        if m:
            res.append((int(m.group(1)), p))
    res.sort(key=lambda t: t[0])
    return res


def ultima_version_disponible() -> int:
    disp = _migraciones_disponibles()
    return disp[-1][0] if disp else 0


def _asegurar_tabla_migraciones(con) -> None:
    con.execute(
        f"""CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
                version    INTEGER PRIMARY KEY,
                name       TEXT    NOT NULL,
                applied_at INTEGER NOT NULL,
                checksum   TEXT    NOT NULL
            )"""
    )
    con.commit()


def _versiones_aplicadas(con) -> set[int]:
    try:
        filas = con.execute(f"SELECT version FROM {MIGRATIONS_TABLE}").fetchall()
        return {int(r[0]) for r in filas}
    except Exception:
        return set()


def version_actual(db_path: Path | str | None = None) -> int:
    """Versión más alta aplicada en `db_path` (0 si ninguna / no existe)."""
    db_path = Path(db_path) if db_path else paths.DB_CURRENT
    if not Path(db_path).exists():
        return 0
    con = conn.abrir(db_path)
    try:
        aplicadas = _versiones_aplicadas(con)
        return max(aplicadas) if aplicadas else 0
    finally:
        con.close()


def aplicar_pendientes(
    db_path: Path | str | None = None,
    *,
    snapshot_antes: bool = True,
) -> list[int]:
    """
    Aplica TODAS las migraciones pendientes a `db_path` (default current.db).

    - Idempotente: si no hay pendientes, no hace nada.
    - Si la DB ya tenía migraciones aplicadas (producción) y hay pendientes,
      crea un snapshot inmutable ANTES de migrar (rollback posible).
    Devuelve la lista de versiones aplicadas en esta corrida.
    """
    db_path = Path(db_path) if db_path else paths.DB_CURRENT
    paths.asegurar_directorios()

    disponibles = _migraciones_disponibles()
    if not disponibles:
        return []

    con = conn.abrir(db_path)
    aplicadas_ahora: list[int] = []
    try:
        _asegurar_tabla_migraciones(con)
        ya = _versiones_aplicadas(con)
        pendientes = [(v, p) for (v, p) in disponibles if v not in ya]
        if not pendientes:
            return []

        # Snapshot pre-migración SOLO si ya había estado previo (datos a proteger).
        if snapshot_antes and ya:
            con.close()  # cerrar para snapshot consistente
            from core.memory.store import snapshots
            snapshots.crear_snapshot(db_path, motivo="pre_migracion")
            con = conn.abrir(db_path)
            _asegurar_tabla_migraciones(con)

        for version, ruta in pendientes:
            sql = ruta.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
            con.executescript(sql)
            con.execute(
                f"INSERT INTO {MIGRATIONS_TABLE} (version, name, applied_at, checksum) "
                f"VALUES (?,?,?,?)",
                (version, ruta.name, int(time.time()), checksum),
            )
            con.commit()
            aplicadas_ahora.append(version)
    finally:
        con.close()

    return aplicadas_ahora
