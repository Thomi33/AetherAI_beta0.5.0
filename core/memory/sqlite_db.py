"""
Gestión de conexión SQLite y operaciones de DB.
"""
import sqlite3
from pathlib import Path

from core.config.settings import RUTA_DB


def inicializar_db():
    """Inicializa la base de datos SQLite con esquema."""
    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS conversaciones (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            rol   TEXT NOT NULL,
            texto TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS comandos (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            orden TEXT NOT NULL,
            cmd   TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS recuerdos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha       TEXT NOT NULL,
            categoria   TEXT,
            contenido   TEXT,
            importancia INTEGER DEFAULT 1
        )
    """)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.commit()
    con.close()


def get_db_connection():
    """Retorna una conexión a SQLite con row_factory configurado."""
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    return con
