"""
Esquema CANÓNICO e inmutable del subsistema de memoria de Aether.

Esta es la ÚNICA fuente de verdad sobre qué tablas y columnas existen en
producción. El guard de integridad (guard.py) y la write-API (store.py) se
basan EXCLUSIVAMENTE en estas definiciones:

- Ninguna columna fuera de estos sets puede escribirse.
- El esquema NO cambia sin una migración versionada (migrations/NNNN_*.sql)
  que además incremente SCHEMA_VERSION.

Si necesitás cambiar el esquema: agregá una migración nueva, actualizá estas
constantes y subí SCHEMA_VERSION. NUNCA edites columnas con find/replace ni
dejes que el agente altere el esquema en runtime.
"""
from __future__ import annotations

# Versión del esquema de producción. Debe coincidir con la última migración
# aplicada (ver migrations/ y migrations.py::ultima_version_disponible()).
SCHEMA_VERSION = 1

# Valores permitidos para memories.type (enum lógico; reforzado por CHECK en SQL).
MEMORY_TYPES = ("fact", "preference", "event")

# Esquema canónico: tabla -> tupla ORDENADA de columnas permitidas.
# El orden se usa para construir los INSERT de forma determinista.
SCHEMA: dict[str, tuple[str, ...]] = {
    "conversations": ("id", "timestamp", "content", "source"),
    "memories":      ("id", "type", "content", "importance", "created_at", "tags"),
    "commands":      ("id", "command", "result", "timestamp"),
}

# Tabla interna del runner de migraciones (no es de "memoria", es metadata).
MIGRATIONS_TABLE = "schema_migrations"

# Tablas que legítimamente pueden existir en la DB: las canónicas + metadata.
TABLAS_PERMITIDAS = frozenset(SCHEMA.keys()) | {MIGRATIONS_TABLE}


def columnas_de(tabla: str) -> tuple[str, ...]:
    """Columnas permitidas para `tabla`. KeyError si la tabla no es canónica."""
    return SCHEMA[tabla]


def es_tabla_canonica(tabla: str) -> bool:
    return tabla in SCHEMA
