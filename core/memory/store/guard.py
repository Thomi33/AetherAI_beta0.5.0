"""
Guard de integridad del subsistema de memoria.

Es la barrera que IMPIDE las corrupciones que motivaron este rediseño
(columnas inválidas tipo 'categorAether', renames globales, cambios implícitos
de esquema). Toda escritura de la write-API pasa por acá ANTES de tocar la DB.

Reglas:
- Solo se aceptan tablas y columnas del esquema CANÓNICO (schema.py). Cualquier
  columna desconocida se rechaza.
- Se rechazan nombres de columna "sospechosos" (no snake_case minúscula, con
  mayúsculas/espacios/caracteres raros), que es la firma típica de un
  find/replace mal aplicado (p.ej. 'categorAether', 'importancAether').
- Se detecta DRIFT: si las columnas REALES de una tabla no coinciden con el
  esquema canónico, se bloquea la operación (esquema cambió sin migración).
"""
from __future__ import annotations

import re
import sqlite3
from typing import Iterable

from core.memory.store import connection as conn
from core.memory.store.schema import SCHEMA, columnas_de, es_tabla_canonica

# Un identificador de columna "sano": minúsculas, empieza con letra, solo
# [a-z0-9_]. Las columnas canónicas cumplen esto; cualquier desvío es sospechoso.
_COLUMNA_SANA = re.compile(r"^[a-z][a-z0-9_]*$")


class SchemaIntegrityError(Exception):
    """Se intentó una operación que viola el esquema canónico."""


class SchemaDriftError(SchemaIntegrityError):
    """Las columnas reales de la DB no coinciden con el esquema canónico."""


def es_columna_sospechosa(nombre: str) -> bool:
    """
    True si el nombre de columna NO tiene la forma sana esperada
    (snake_case minúscula). Atrapa variantes corruptas como 'categorAether'
    (tiene mayúscula) o nombres con espacios/símbolos.
    """
    return not isinstance(nombre, str) or not _COLUMNA_SANA.match(nombre)


def validar_columnas(tabla: str, columnas: Iterable[str]) -> None:
    """
    Valida que `columnas` sean exactamente columnas permitidas de `tabla`.

    Lanza SchemaIntegrityError si:
    - la tabla no es canónica;
    - alguna columna no pertenece al esquema de la tabla;
    - alguna columna tiene un nombre sospechoso (firma de corrupción).
    """
    if not es_tabla_canonica(tabla):
        raise SchemaIntegrityError(f"Tabla no permitida: {tabla!r}")

    permitidas = set(columnas_de(tabla))
    for col in columnas:
        if es_columna_sospechosa(col):
            raise SchemaIntegrityError(
                f"Columna sospechosa rechazada en {tabla!r}: {col!r} "
                f"(¿rename/replace mal aplicado?)."
            )
        if col not in permitidas:
            raise SchemaIntegrityError(
                f"Columna desconocida {col!r} para tabla {tabla!r}. "
                f"Permitidas: {sorted(permitidas)}. "
                f"Cambiar el esquema requiere una migración versionada."
            )


def verificar_tabla(con: sqlite3.Connection, tabla: str) -> None:
    """
    Verifica que las columnas REALES de `tabla` coincidan EXACTAMENTE con el
    esquema canónico (mismo conjunto). Detecta drift / corrupción de esquema.

    Lanza SchemaDriftError si hay columnas de más, de menos o sospechosas.
    """
    if not es_tabla_canonica(tabla):
        raise SchemaIntegrityError(f"Tabla no permitida: {tabla!r}")

    reales = conn.columnas_reales(con, tabla)
    if not reales:
        raise SchemaDriftError(f"La tabla {tabla!r} no existe (falta migrar).")

    sospechosas = [c for c in reales if es_columna_sospechosa(c)]
    if sospechosas:
        raise SchemaDriftError(
            f"La tabla {tabla!r} tiene columnas corruptas/sospechosas: {sospechosas}. "
            f"Esquema esperado: {list(columnas_de(tabla))}."
        )

    if set(reales) != set(columnas_de(tabla)):
        raise SchemaDriftError(
            f"Drift de esquema en {tabla!r}: reales={reales} != "
            f"canónico={list(columnas_de(tabla))}. Falta una migración."
        )


def verificar_base(con: sqlite3.Connection) -> None:
    """
    Verifica la coherencia de TODA la base: cada tabla canónica existe con sus
    columnas correctas, y no hay tablas con nombres sospechosos.
    """
    for tabla in SCHEMA:
        verificar_tabla(con, tabla)

    for t in conn.tablas_reales(con):
        # Las tablas canónicas + 'schema_migrations' son válidas; el resto,
        # si tiene nombre sospechoso, es señal de manipulación.
        if es_columna_sospechosa(t) and t != "schema_migrations":
            raise SchemaDriftError(f"Tabla con nombre sospechoso: {t!r}")
