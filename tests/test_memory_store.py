#!/usr/bin/env python3
"""
Tests del subsistema de memoria controlado (core/memory/store).

Cubre los 8 requisitos: DB única current.db, esquema fijo + migraciones
versionadas, guard de integridad anti-corrupción (rechaza 'categorAether' y
columnas desconocidas/drift), write-API (validar→normalizar→dedupe→escribir),
snapshots inmutables + rollback, y staging.

Aislado en un tmpdir (parchea core.memory.store.paths). No requiere Ollama.

Ejecutar: python tests/test_memory_store.py
"""
import sqlite3
import sys
import tempfile
import shutil
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory.store import paths, migrations, guard, snapshots
from core.memory.store.store import MemoryStore
from core.memory.store.schema import SCHEMA
from core.memory.store.legacy_import import importar_legacy


@contextmanager
def entorno():
    """tmpdir con paths parcheados; current.db migrado limpio."""
    tmp = Path(tempfile.mkdtemp(prefix="aether_mem_"))
    orig = {k: getattr(paths, k) for k in ("DB_CURRENT", "DB_STAGING", "SNAPSHOTS_DIR", "BACKUPS_DIR", "DB_LEGACY")}
    paths.DB_CURRENT = tmp / "current.db"
    paths.DB_STAGING = tmp / "staging.db"
    paths.SNAPSHOTS_DIR = tmp / "snapshots"
    paths.BACKUPS_DIR = tmp / "backups"
    paths.DB_LEGACY = tmp / "memoria.db"
    try:
        migrations.aplicar_pendientes(paths.DB_CURRENT)
        yield tmp
    finally:
        for k, v in orig.items():
            setattr(paths, k, v)
        shutil.rmtree(tmp, ignore_errors=True)


# ── Migraciones / esquema ─────────────────────────────────────────────

def test_migracion_crea_esquema_y_es_idempotente():
    with entorno():
        assert migrations.version_actual(paths.DB_CURRENT) == 1
        # idempotente: re-aplicar no agrega nada
        assert migrations.aplicar_pendientes(paths.DB_CURRENT) == []
        con = sqlite3.connect(paths.DB_CURRENT)
        try:
            tablas = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"conversations", "memories", "commands", "schema_migrations"} <= tablas
            cols = [r[1] for r in con.execute("PRAGMA table_info(memories)")]
            assert cols == list(SCHEMA["memories"]), cols
        finally:
            con.close()


def test_verificar_base_ok():
    with entorno():
        MemoryStore(paths.DB_CURRENT).verificar()  # no debe lanzar


# ── Guard de integridad ────────────────────────────────────────────────

def test_guard_detecta_columna_sospechosa():
    assert guard.es_columna_sospechosa("categorAether") is True
    assert guard.es_columna_sospechosa("importancAether") is True
    assert guard.es_columna_sospechosa("Type") is True
    assert guard.es_columna_sospechosa("drop table") is True
    assert guard.es_columna_sospechosa("type") is False
    assert guard.es_columna_sospechosa("created_at") is False


def test_guard_rechaza_columna_desconocida():
    try:
        guard.validar_columnas("memories", ["id", "categoria"])  # categoria no existe en el nuevo esquema
    except guard.SchemaIntegrityError:
        pass
    else:
        raise AssertionError("debió rechazar columna desconocida 'categoria'")


def test_guard_rechaza_columna_corrupta():
    try:
        guard.validar_columnas("memories", ["id", "categorAether"])
    except guard.SchemaIntegrityError:
        pass
    else:
        raise AssertionError("debió rechazar 'categorAether'")


def test_guard_detecta_drift_en_db():
    with entorno() as tmp:
        # DB con una tabla 'memories' corrupta (columna categorAether)
        bad = tmp / "bad.db"
        con = sqlite3.connect(bad)
        con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, categorAether TEXT)")
        con.commit(); con.close()
        con = sqlite3.connect(bad)
        try:
            try:
                guard.verificar_tabla(con, "memories")
            except guard.SchemaDriftError:
                pass
            else:
                raise AssertionError("debió detectar drift/corrupción")
        finally:
            con.close()


# ── Write-API: validación / normalización ──────────────────────────────

def test_add_y_lectura_basica():
    with entorno():
        s = MemoryStore(paths.DB_CURRENT, snapshot_interval=0)
        cid = s.add_conversation("Hola", source="usuario")
        assert cid and len(cid) == 32  # uuid hex
        s.add_memory("le gusta el mate", type="preference", importance=8, tags=["gustos", "bebida"])
        s.add_command("ls -la", result="ok")
        assert s.count("conversations") == 1
        assert s.count("memories") == 1
        assert s.count("commands") == 1
        m = s.get_memories()[0]
        assert m["type"] == "preference" and m["importance"] == 8 and m["tags"] == "gustos,bebida"
        c = s.get_conversations()[0]
        assert c["content"] == "Hola" and isinstance(c["timestamp"], int)


def test_validaciones():
    with entorno():
        s = MemoryStore(paths.DB_CURRENT, snapshot_interval=0)
        # type inválido
        try:
            s.add_memory("x", type="categoria")
        except ValueError:
            pass
        else:
            raise AssertionError("type inválido debió lanzar ValueError")
        # content vacío
        try:
            s.add_conversation("   ")
        except ValueError:
            pass
        else:
            raise AssertionError("content vacío debió lanzar ValueError")
        # importance fuera de rango → clamp a 10
        s.add_memory("muy importante", type="fact", importance=999)
        assert s.get_memories()[0]["importance"] == 10


def test_dedupe():
    with entorno():
        s = MemoryStore(paths.DB_CURRENT, snapshot_interval=0)
        # memories: idempotente por (type, content)
        assert s.add_memory("dato", type="fact") is not None
        assert s.add_memory("dato", type="fact") is None
        assert s.count("memories") == 1
        # conversations: dedupe del último consecutivo idéntico
        assert s.add_conversation("hola", source="usuario") is not None
        assert s.add_conversation("hola", source="usuario") is None
        assert s.add_conversation("chau", source="usuario") is not None
        assert s.count("conversations") == 2


# ── Snapshots / rollback ───────────────────────────────────────────────

def test_snapshot_inmutable_y_rollback():
    with entorno():
        s = MemoryStore(paths.DB_CURRENT, snapshot_interval=0)
        s.add_memory("antes del snapshot", type="fact")
        snap = snapshots.crear_snapshot(paths.DB_CURRENT, motivo="test")
        assert snap and snap.exists()
        # inmutable: no se puede escribir el archivo de snapshot
        try:
            with open(snap, "ab") as f:
                f.write(b"x")
            escribible = True
        except PermissionError:
            escribible = False
        assert escribible is False, "el snapshot debería ser read-only"

        # cambia el estado, luego rollback al snapshot
        s.add_memory("despues del snapshot", type="event")
        assert s.count("memories") == 2
        snapshots.rollback(snap, paths.DB_CURRENT)
        assert MemoryStore(paths.DB_CURRENT).count("memories") == 1


# ── Staging separado de producción ─────────────────────────────────────

def test_staging_no_toca_produccion():
    with entorno():
        migrations.aplicar_pendientes(paths.DB_STAGING, snapshot_antes=False)
        prod = MemoryStore(paths.DB_CURRENT, snapshot_interval=0)
        stg = MemoryStore(paths.DB_STAGING, snapshot_interval=0)
        stg.add_memory("solo en staging", type="fact")
        assert stg.count("memories") == 1
        assert prod.count("memories") == 0


# ── Import legacy ───────────────────────────────────────────────────────

def test_import_legacy_mapea_y_no_propaga_corrupcion():
    with entorno() as tmp:
        legacy = tmp / "memoria.db"
        con = sqlite3.connect(legacy)
        con.executescript("""
            CREATE TABLE conversaciones (id INTEGER PRIMARY KEY, fecha TEXT, rol TEXT, texto TEXT);
            CREATE TABLE comandos (id INTEGER PRIMARY KEY, fecha TEXT, orden TEXT, cmd TEXT);
            CREATE TABLE recuerdos (id INTEGER PRIMARY KEY, fecha TEXT, categoria TEXT, contenido TEXT, importancia INTEGER);
        """)
        con.execute("INSERT INTO conversaciones (fecha,rol,texto) VALUES (?,?,?)",
                    ("2026-06-25T11:08:00", "usuario", "hola viejo"))
        con.execute("INSERT INTO comandos (fecha,orden,cmd) VALUES (?,?,?)",
                    ("2026-06-25 11:09", "abre firefox", "flatpak run org.mozilla.firefox &"))
        con.execute("INSERT INTO recuerdos (fecha,categoria,contenido,importancia) VALUES (?,?,?,?)",
                    ("2026-06-25T10:00:00", "preferencias", '{"nombre":"Thomas"}', 10))
        con.execute("INSERT INTO recuerdos (fecha,categoria,contenido,importancia) VALUES (?,?,?,?)",
                    ("2026-06-25T10:01:00", "hardware", "tiene una RTX", 5))
        con.commit(); con.close()

        store = MemoryStore(paths.DB_CURRENT, snapshot_interval=0)
        res = importar_legacy(legacy, store)
        assert res == {"conversations": 1, "commands": 1, "memories": 2}, res

        # categoria→type correcto y SIN columnas corruptas
        prefs = store.get_memories(type="preference")
        facts = store.get_memories(type="fact")
        assert len(prefs) == 1 and "Thomas" in prefs[0]["content"]
        assert len(facts) == 1 and facts[0]["content"] == "tiene una RTX"
        store.verificar()  # esquema sigue íntegro tras importar


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fallos = 0
    for fn in fns:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except Exception as e:
            import traceback
            fallos += 1
            print(f"❌ {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - fallos}/{len(fns)} tests OK")
    sys.exit(1 if fallos else 0)
