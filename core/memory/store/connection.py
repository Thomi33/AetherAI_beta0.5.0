"""
Apertura de conexiones SQLite del subsistema de memoria.

Centraliza los PRAGMAs (WAL, foreign_keys, synchronous) y ofrece una conexión
de SOLO LECTURA para inspección segura. Todas las conexiones de escritura las
abre y cierra la write-API por operación (corta vida → seguro con WAL).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def abrir(db_path: str | Path) -> sqlite3.Connection:
    """
    Abre una conexión de lectura/escritura con PRAGMAs estándar.
    row_factory = sqlite3.Row para acceso por nombre de columna.
    """
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def abrir_solo_lectura(db_path: str | Path) -> sqlite3.Connection:
    """
    Abre una conexión de SOLO LECTURA (mode=ro). Útil para inspección/guard
    sin riesgo de escritura accidental. Falla si el archivo no existe.
    """
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def columnas_reales(con: sqlite3.Connection, tabla: str) -> list[str]:
    """Devuelve las columnas REALES de una tabla según PRAGMA table_info."""
    return [r[1] for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()]


def tablas_reales(con: sqlite3.Connection) -> list[str]:
    """Devuelve los nombres de tablas reales (excluye internas sqlite_*)."""
    return [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
