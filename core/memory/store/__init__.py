"""
Subsistema de memoria controlado de Aether.

DB única de producción (current.db) con esquema fijo y versionado, write-API
obligatoria con guard de integridad anti-corrupción, snapshots inmutables con
rollback y DB de staging para pruebas.

Uso típico:
    from core.memory.store import get_store, inicializar_produccion
    inicializar_produccion()              # aplica migraciones (explícito)
    store = get_store()
    store.add_conversation("hola", source="usuario")
    store.add_memory("le gusta el mate", type="preference", importance=8)
"""
from core.memory.store.store import (
    MemoryStore,
    get_store,
    get_staging_store,
    inicializar_produccion,
)
from core.memory.store.guard import (
    SchemaIntegrityError,
    SchemaDriftError,
)
from core.memory.store import snapshots, migrations

__all__ = [
    "MemoryStore",
    "get_store",
    "get_staging_store",
    "inicializar_produccion",
    "SchemaIntegrityError",
    "SchemaDriftError",
    "snapshots",
    "migrations",
]
