"""
Write-API del subsistema de memoria — ÚNICA vía de escritura.

El agente y el resto del código NO tocan SQLite directamente: usan MemoryStore.
Cada escritura sigue el flujo obligatorio:

    validar estructura → normalizar datos → verificar duplicados →
    escribir en current.db → crear snapshot si corresponde

La validación se apoya en el guard (allowlist de columnas + anti-corrupción +
detección de drift de esquema). Los INSERT usan SIEMPRE una lista FIJA de
columnas derivada del esquema canónico → es imposible crear columnas dinámicas
o aplicar renames implícitos desde acá.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from core.memory.store import connection as conn
from core.memory.store import guard, migrations, paths, snapshots
from core.memory.store.schema import MEMORY_TYPES, columnas_de


def _uuid() -> str:
    return uuid.uuid4().hex


def _ahora() -> int:
    return int(time.time())


class MemoryStore:
    """
    Acceso controlado a una DB de memoria (por defecto current.db).

    Parámetros:
        db_path: ruta de la DB (default: current.db de producción).
        snapshot_interval: segundos entre snapshots automáticos en escritura
            (0 = desactivado, p.ej. para importadores/tests).
    """

    def __init__(self, db_path: Path | str | None = None, *, snapshot_interval: int = 3600):
        self.db_path = Path(db_path) if db_path else paths.DB_CURRENT
        self.snapshot_interval = snapshot_interval

    # ── infraestructura ──────────────────────────────────────────────
    def inicializar(self) -> list[int]:
        """Aplica migraciones pendientes (explícito). Devuelve versiones nuevas."""
        return migrations.aplicar_pendientes(self.db_path)

    def verificar(self) -> None:
        """Verifica integridad del esquema (lanza SchemaDriftError si hay drift)."""
        con = conn.abrir(self.db_path)
        try:
            guard.verificar_base(con)
        finally:
            con.close()

    # ── escritura (API pública) ──────────────────────────────────────
    def add_conversation(
        self, content: str, source: str | None = None, *,
        id: str | None = None, timestamp: int | None = None,
    ) -> str | None:
        contenido = self._texto_obligatorio(content, "content")
        fila = {
            "id": id or _uuid(),
            "timestamp": int(timestamp) if timestamp is not None else _ahora(),
            "content": contenido,
            "source": (str(source).strip() if source is not None else None),
        }
        return self._insertar("conversations", fila, dedupe=("content", "source"))

    def add_memory(
        self, content: str, type: str, *,
        importance: int = 0, tags: str | list[str] | None = None,
        id: str | None = None, created_at: int | None = None,
    ) -> str | None:
        contenido = self._texto_obligatorio(content, "content")
        if type not in MEMORY_TYPES:
            raise ValueError(f"type inválido: {type!r}. Permitidos: {MEMORY_TYPES}")
        try:
            imp = int(importance)
        except (TypeError, ValueError):
            imp = 0
        imp = max(0, min(10, imp))  # clamp 0..10
        if isinstance(tags, (list, tuple)):
            tags = ",".join(str(t).strip() for t in tags if str(t).strip()) or None
        fila = {
            "id": id or _uuid(),
            "type": type,
            "content": contenido,
            "importance": imp,
            "created_at": int(created_at) if created_at is not None else _ahora(),
            "tags": (str(tags) if tags else None),
        }
        return self._insertar("memories", fila, dedupe=("type", "content"))

    def add_command(
        self, command: str, *, result: str | None = None,
        id: str | None = None, timestamp: int | None = None,
    ) -> str | None:
        cmd = self._texto_obligatorio(command, "command")
        fila = {
            "id": id or _uuid(),
            "command": cmd,
            "result": (str(result) if result is not None else None),
            "timestamp": int(timestamp) if timestamp is not None else _ahora(),
        }
        return self._insertar("commands", fila, dedupe=("command",))

    # ── lectura ──────────────────────────────────────────────────────
    def get_conversations(self, limit: int = 100, *, orden: str = "asc") -> list[dict]:
        direccion = "ASC" if orden.lower() == "asc" else "DESC"
        # Tomamos los `limit` más recientes y, si se pide asc, los devolvemos cronológicos.
        sql = ("SELECT id, timestamp, content, source FROM conversations "
               "ORDER BY timestamp DESC, rowid DESC LIMIT ?")
        filas = self._consultar(sql, (limit,))
        if direccion == "ASC":
            filas = list(reversed(filas))
        return filas

    def get_memories(self, *, type: str | None = None,
                     min_importance: int = 0, limit: int = 20) -> list[dict]:
        sql = ("SELECT id, type, content, importance, created_at, tags "
               "FROM memories WHERE importance >= ?")
        params: list = [int(min_importance)]
        if type is not None:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY importance DESC, created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return self._consultar(sql, tuple(params))

    def get_commands(self, limit: int = 50, *, orden: str = "asc") -> list[dict]:
        filas = self._consultar(
            "SELECT id, command, result, timestamp FROM commands "
            "ORDER BY timestamp DESC, rowid DESC LIMIT ?", (limit,)
        )
        if orden.lower() == "asc":
            filas = list(reversed(filas))
        return filas

    def count(self, tabla: str) -> int:
        """Cuenta filas de una tabla canónica (rechaza tablas no permitidas)."""
        from core.memory.store.schema import es_tabla_canonica
        if not es_tabla_canonica(tabla):
            raise guard.SchemaIntegrityError(f"Tabla no permitida: {tabla!r}")
        return self._consultar(f"SELECT COUNT(*) AS n FROM {tabla}")[0]["n"]

    # ── interno ──────────────────────────────────────────────────────
    @staticmethod
    def _texto_obligatorio(valor, campo: str) -> str:
        if not isinstance(valor, str):
            valor = "" if valor is None else str(valor)
        valor = valor.strip()
        if not valor:
            raise ValueError(f"{campo} no puede estar vacío")
        return valor

    def _consultar(self, sql: str, params: tuple = ()) -> list[dict]:
        con = conn.abrir(self.db_path)
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    def _es_duplicado(self, con, tabla: str, fila: dict, claves: tuple[str, ...]) -> bool:
        """
        Dedupe simple:
        - conversations/commands: duplicado si el ÚLTIMO registro coincide en
          todas las `claves` (evita doble-log del mismo turno/comando).
        - memories: duplicado si EXISTE cualquier fila con esas claves
          (hechos/preferencias idempotentes).
        """
        cond = " AND ".join(f"{k} IS ?" for k in claves)
        valores = tuple(fila[k] for k in claves)
        if tabla == "memories":
            sql = f"SELECT 1 FROM {tabla} WHERE {cond} LIMIT 1"
            return con.execute(sql, valores).fetchone() is not None
        # conversations / commands → comparar contra el más reciente
        orden_col = "timestamp"
        sql = f"SELECT {', '.join(claves)} FROM {tabla} ORDER BY {orden_col} DESC, rowid DESC LIMIT 1"
        ultimo = con.execute(sql).fetchone()
        if ultimo is None:
            return False
        return tuple(ultimo[k] for k in claves) == valores

    def _insertar(self, tabla: str, fila: dict, *, dedupe: tuple[str, ...] = ()) -> str | None:
        """
        Flujo obligatorio de escritura. Devuelve el id insertado, o None si se
        omitió por duplicado.
        """
        # 1) VALIDAR estructura (allowlist + anti-corrupción)
        guard.validar_columnas(tabla, fila.keys())
        cols = [c for c in columnas_de(tabla) if c in fila]  # orden canónico

        con = conn.abrir(self.db_path)
        try:
            # 2) VERIFICAR que el esquema real no haya driftado/corrompido
            guard.verificar_tabla(con, tabla)

            # 3) DEDUPE
            if dedupe and self._es_duplicado(con, tabla, fila, dedupe):
                return None

            # 4) ESCRIBIR (lista de columnas FIJA y canónica → sin columnas dinámicas)
            placeholders = ", ".join("?" for _ in cols)
            sql = f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({placeholders})"
            con.execute(sql, tuple(fila[c] for c in cols))
            con.commit()
        finally:
            con.close()

        # 5) SNAPSHOT si corresponde (no bloquea la escritura si falla)
        self._maybe_snapshot()
        return fila["id"]

    def _maybe_snapshot(self) -> None:
        if self.snapshot_interval <= 0:
            return
        try:
            reciente = snapshots.snapshot_mas_reciente()
            if reciente is not None:
                edad = time.time() - reciente.stat().st_mtime
                if edad < self.snapshot_interval:
                    return
            snapshots.crear_snapshot(self.db_path, motivo="auto")
        except Exception:
            pass  # un fallo de snapshot nunca debe romper la escritura


# ── Singletons de conveniencia ───────────────────────────────────────
_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    """MemoryStore de producción (current.db)."""
    global _store
    if _store is None:
        _store = MemoryStore(paths.DB_CURRENT)
    return _store


def get_staging_store() -> MemoryStore:
    """MemoryStore de staging (staging.db), sin snapshots automáticos."""
    return MemoryStore(paths.DB_STAGING, snapshot_interval=0)


def inicializar_produccion() -> list[int]:
    """Aplica migraciones a current.db (y prepara staging). Explícito."""
    paths.asegurar_directorios()
    aplicadas = migrations.aplicar_pendientes(paths.DB_CURRENT)
    # staging se migra al mismo esquema para poder probar cambios ahí.
    migrations.aplicar_pendientes(paths.DB_STAGING, snapshot_antes=False)
    return aplicadas
