"""
memory_manager.py — Gestor de memoria de Aether.
Habla directo con la DB de producción (current.db). Sin capas intermedias.

Tablas esperadas:
  conversaciones  — historial filtrable por sesión y tema
  comandos        — comandos shell ejecutados
  recuerdos       — hechos permanentes (archival memory)
  core_memory     — estado siempre presente (se auto-crea si no existe)
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.config.settings import BASE_AETHER

# ── Ruta de la DB ────────────────────────────────────────────────────
# Antes apuntaba a /mnt/basurero/Javier/db/memoria.db (ruta vieja).
# Ahora usa la DB de producción documentada en el README.
DB_PATH = Path(BASE_AETHER) / "db" / "current.db"

# ── Límites ──────────────────────────────────────────────────────────
MAX_HISTORIAL_RAM = 200   # turnos que se mantienen en el dict RAM
MAX_TEXTO         = 1000  # caracteres máximos por turno/comando


# ══════════════════════════════════════════════════════════════════════
# CONEXIÓN
# ══════════════════════════════════════════════════════════════════════
@contextmanager
def _db():
    """Context manager para conexiones SQLite seguras."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════
# ESQUEMA — asegura que las tablas existen (idempotente)
# ══════════════════════════════════════════════════════════════════════
def asegurar_esquema() -> None:
    """
    Crea las tablas que memory_manager necesita si no existen.
    Llama a esto UNA VEZ al arrancar el agente (antes de cargar_memoria).
    """
    try:
        with _db() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS conversaciones (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha      TEXT,
                    rol        TEXT,
                    texto      TEXT,
                    sesion_id  TEXT DEFAULT '',
                    tema       TEXT DEFAULT '',
                    proyecto   TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS comandos (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha      TEXT,
                    orden      TEXT,
                    cmd        TEXT,
                    sesion_id  TEXT DEFAULT '',
                    exitoso    INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS recuerdos (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha       TEXT,
                    categoria   TEXT DEFAULT '',
                    contenido   TEXT,
                    importancia INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS core_memory (
                    clave        TEXT PRIMARY KEY,
                    valor        TEXT,
                    actualizado  TEXT
                );

                CREATE TABLE IF NOT EXISTS resumen_memoria (
                    id               INTEGER PRIMARY KEY CHECK (id = 1),
                    texto            TEXT DEFAULT '',
                    ultimo_turno_id  INTEGER DEFAULT 0,
                    actualizado      TEXT
                );
            """)
        print("[MEMORIA] Esquema verificado/creado en", DB_PATH)
    except Exception as e:
        print(f"[MEMORIA] ⚠️  No se pudo asegurar esquema: {e}")


# ══════════════════════════════════════════════════════════════════════
# DICT RAM — estructura en memoria durante la sesión
# ══════════════════════════════════════════════════════════════════════
def _memoria_vacia() -> dict:
    return {
        # Contrato legacy que sigue consumiendo node_memory, la TUI y algunos
        # conectores. Se conserva mientras la memoria persistente migra al
        # modelo core/resumen; quitarlo rompía sesiones existentes.
        "preferencias":      {"nombre_usuario": "Thomas", "navegador": "brave", "notas": []},
        "flatpaks":          {},
        "conversacion":       [],   # [{rol, texto, fecha, sesion_id, tema}]
        "historial_comandos": [],   # [{orden, cmd, fecha, sesion_id}]
        "core":               {},   # espejo de core_memory
        "resumen":            "",   # espejo de resumen_memoria.texto (rolling summary)
        "sesion_id":          "",
    }


def normalizar_mem(mem: dict | None) -> dict:
    """Garantiza que el dict RAM tenga la estructura correcta."""
    base = _memoria_vacia()
    if not isinstance(mem, dict):
        return base
    # Mantener el mismo objeto de sesión, pero completar todos los campos del
    # contrato en vez de devolver estructuras parciales según el caller.
    for clave, valor in base.items():
        mem.setdefault(clave, valor.copy() if isinstance(valor, dict) else list(valor) if isinstance(valor, list) else valor)
    if not isinstance(mem.get("preferencias"), dict):
        mem["preferencias"] = dict(base["preferencias"])
    else:
        prefs = mem["preferencias"]
        prefs.setdefault("nombre_usuario", "Thomas")
        prefs.setdefault("navegador", "brave")
        if not isinstance(prefs.get("notas"), list):
            prefs["notas"] = []
    if not isinstance(mem.get("flatpaks"), dict):
        mem["flatpaks"] = {}
    if not isinstance(mem.get("conversacion"), list):
        mem["conversacion"] = []
    if not isinstance(mem.get("historial_comandos"), list):
        mem["historial_comandos"] = []
    if not isinstance(mem.get("core"), dict):
        mem["core"] = {}
    if "sesion_id" not in mem:
        mem["sesion_id"] = ""
    if not isinstance(mem.get("resumen"), str):
        mem["resumen"] = ""
    return mem


# ══════════════════════════════════════════════════════════════════════
# CORE MEMORY — siempre presente en el contexto
# ══════════════════════════════════════════════════════════════════════
def leer_core_memory() -> dict:
    """Lee toda la core_memory como dict {clave: valor}."""
    try:
        with _db() as con:
            filas = con.execute("SELECT clave, valor FROM core_memory").fetchall()
            return {f["clave"]: f["valor"] for f in filas}
    except Exception as e:
        print(f"[MEMORIA] Error leyendo core_memory: {e}")
        return {}


def actualizar_core(clave: str, valor: str) -> None:
    """Actualiza o inserta un valor en core_memory."""
    try:
        with _db() as con:
            con.execute("""
                INSERT INTO core_memory (clave, valor, actualizado)
                VALUES (?, ?, ?)
                ON CONFLICT(clave) DO UPDATE SET
                    valor=excluded.valor,
                    actualizado=excluded.actualizado
            """, (clave, valor, datetime.now().isoformat()))
    except Exception as e:
        print(f"[MEMORIA] Error actualizando core '{clave}': {e}")


# ══════════════════════════════════════════════════════════════════════
# RESUMEN ACUMULATIVO (rolling summary) — reemplaza la inyección de chats
# crudos como fuente de contexto de largo plazo. Se actualiza por
# CONSOLIDACIÓN (core/memory/consolidator.py), no por append: cada corrida
# reescribe el resumen completo incorporando lo nuevo relevante.
# ══════════════════════════════════════════════════════════════════════
def obtener_resumen() -> dict:
    """
    Retorna {"texto": str, "ultimo_turno_id": int, "actualizado": str}.
    Si todavía no hay resumen (primera vez), texto viene vacío.
    """
    try:
        with _db() as con:
            fila = con.execute(
                "SELECT texto, ultimo_turno_id, actualizado FROM resumen_memoria WHERE id = 1"
            ).fetchone()
            if fila:
                return dict(fila)
    except Exception as e:
        print(f"[MEMORIA] Error leyendo resumen: {e}")
    return {"texto": "", "ultimo_turno_id": 0, "actualizado": ""}


def guardar_resumen(texto: str, ultimo_turno_id: int | None = None) -> None:
    """
    Reemplaza el resumen acumulativo completo (fila única id=1).

    Si ultimo_turno_id es None, preserva el que ya estaba guardado (útil
    para ediciones manuales desde /memory en la TUI, donde no corresponde
    tocar el cursor de consolidación automática).
    """
    try:
        with _db() as con:
            if ultimo_turno_id is None:
                fila = con.execute(
                    "SELECT ultimo_turno_id FROM resumen_memoria WHERE id = 1"
                ).fetchone()
                ultimo_turno_id = fila["ultimo_turno_id"] if fila else 0
            con.execute("""
                INSERT INTO resumen_memoria (id, texto, ultimo_turno_id, actualizado)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    texto=excluded.texto,
                    ultimo_turno_id=excluded.ultimo_turno_id,
                    actualizado=excluded.actualizado
            """, (texto, ultimo_turno_id, datetime.now().isoformat()))
        print(f"[MEMORIA] Resumen actualizado ({len(texto)} chars, hasta turno #{ultimo_turno_id}).")
    except Exception as e:
        print(f"[MEMORIA] Error guardando resumen: {e}")


def obtener_turnos_pendientes_de_resumen(limit: int = 300) -> list[dict]:
    """
    Turnos crudos de `conversaciones` posteriores al último consolidado en
    el resumen. Usado por el consolidador para saber qué integrar.
    """
    ultimo_id = obtener_resumen().get("ultimo_turno_id", 0)
    try:
        with _db() as con:
            filas = con.execute("""
                SELECT id, fecha, rol, texto
                FROM conversaciones
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
            """, (ultimo_id, limit)).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        print(f"[MEMORIA] Error leyendo turnos pendientes de resumen: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# CARGA INICIAL
# ══════════════════════════════════════════════════════════════════════
def cargar_memoria() -> dict:
    """
    Carga el dict RAM desde current.db.
    Se llama una vez al iniciar el agente.
    Si falla, devuelve memoria vacía (nunca rompe el arranque).
    """
    mem = _memoria_vacia()
    try:
        with _db() as con:
            # Core memory
            filas = con.execute("SELECT clave, valor FROM core_memory").fetchall()
            mem["core"] = {f["clave"]: f["valor"] for f in filas}

            # Resumen acumulativo (rolling summary)
            fila_resumen = con.execute(
                "SELECT texto FROM resumen_memoria WHERE id = 1"
            ).fetchone()
            mem["resumen"] = fila_resumen["texto"] if fila_resumen else ""

            # Últimos turnos conversacionales
            turnos = con.execute("""
                SELECT fecha, rol, texto, sesion_id, tema
                FROM conversaciones
                ORDER BY id DESC
                LIMIT ?
            """, (MAX_HISTORIAL_RAM,)).fetchall()
            mem["conversacion"] = [
                {
                    "rol":       t["rol"],
                    "texto":     t["texto"],
                    "fecha":     t["fecha"],
                    "sesion_id": t["sesion_id"],
                    "tema":      t["tema"],
                }
                for t in reversed(turnos)
            ]

            # Últimos comandos
            cmds = con.execute("""
                SELECT fecha, orden, cmd, sesion_id
                FROM comandos
                ORDER BY id DESC
                LIMIT 50
            """).fetchall()
            mem["historial_comandos"] = [
                {
                    "orden":     c["orden"],
                    "cmd":       c["cmd"],
                    "fecha":     c["fecha"],
                    "sesion_id": c["sesion_id"],
                }
                for c in reversed(cmds)
            ]
    except Exception as e:
        print(f"[MEMORIA] Error cargando memoria: {e}")

    return normalizar_mem(mem)


# ══════════════════════════════════════════════════════════════════════
# CONVERSACIÓN
# ══════════════════════════════════════════════════════════════════════
def registrar_turno(
    mem: dict,
    rol: str,
    texto: str,
    sesion_id: str = "",
    tema: str = "",
    proyecto: str = "",
) -> None:
    """Registra un turno en DB y en el dict RAM."""
    texto = texto[:MAX_TEXTO]
    fecha = datetime.now().isoformat()
    try:
        with _db() as con:
            con.execute("""
                INSERT INTO conversaciones (fecha, rol, texto, sesion_id, tema, proyecto)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (fecha, rol, texto, sesion_id, tema, proyecto))
        print(f"[MEMORIA] Turno guardado | rol={rol} | tema={tema} | texto={texto[:40]}...")
    except Exception as e:
        print(f"[MEMORIA] Error registrando turno: {e}")

    if not isinstance(mem.get("conversacion"), list):
        mem["conversacion"] = []
    mem["conversacion"].append({
        "rol": rol, "texto": texto,
        "fecha": fecha, "sesion_id": sesion_id, "tema": tema,
    })
    mem["conversacion"] = mem["conversacion"][-MAX_HISTORIAL_RAM:]


# ══════════════════════════════════════════════════════════════════════
# COMANDOS
# ══════════════════════════════════════════════════════════════════════
def registrar_comando(
    mem: dict,
    orden: str,
    cmd: str,
    sesion_id: str = "",
    exitoso: bool = True,
) -> None:
    """Registra un comando shell en DB y en el dict RAM."""
    cmd   = cmd[:MAX_TEXTO]
    orden = orden[:MAX_TEXTO]
    fecha = datetime.now().isoformat()
    try:
        with _db() as con:
            con.execute("""
                INSERT INTO comandos (fecha, orden, cmd, sesion_id, exitoso)
                VALUES (?, ?, ?, ?, ?)
            """, (fecha, orden, cmd, sesion_id, int(exitoso)))
    except Exception as e:
        print(f"[MEMORIA] Error registrando comando: {e}")

    mem["historial_comandos"].append({
        "orden": orden, "cmd": cmd,
        "fecha": fecha, "sesion_id": sesion_id,
    })
    mem["historial_comandos"] = mem["historial_comandos"][-50:]


# ══════════════════════════════════════════════════════════════════════
# RECUERDOS (archival memory)
# ══════════════════════════════════════════════════════════════════════
def guardar_recuerdo(
    contenido: str,
    categoria: str = "",
    importancia: int = 1,
) -> None:
    """Guarda un hecho permanente en recuerdos."""
    try:
        with _db() as con:
            con.execute("""
                INSERT INTO recuerdos (fecha, categoria, contenido, importancia)
                VALUES (?, ?, ?, ?)
            """, (datetime.now().isoformat(), categoria, contenido[:MAX_TEXTO], importancia))
    except Exception as e:
        print(f"[MEMORIA] Error guardando recuerdo: {e}")


def obtener_recuerdos(
    categoria: str | None = None,
    importancia_min: int = 1,
    limit: int = 20,
) -> list[dict]:
    """Lee recuerdos filtrando por categoría e importancia mínima."""
    try:
        with _db() as con:
            if categoria:
                filas = con.execute("""
                    SELECT fecha, categoria, contenido, importancia
                    FROM recuerdos
                    WHERE categoria = ? AND importancia >= ?
                    ORDER BY importancia DESC, id DESC
                    LIMIT ?
                """, (categoria, importancia_min, limit)).fetchall()
            else:
                filas = con.execute("""
                    SELECT fecha, categoria, contenido, importancia
                    FROM recuerdos
                    WHERE importancia >= ?
                    ORDER BY importancia DESC, id DESC
                    LIMIT ?
                """, (importancia_min, limit)).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        print(f"[MEMORIA] Error leyendo recuerdos: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# RECUPERACIÓN POR RELEVANCIA (para el Context Manager)
# ══════════════════════════════════════════════════════════════════════
def obtener_turnos_por_tema(tema: str, sesion_id: str = "", limit: int = 10) -> list[dict]:
    """
    Recupera turnos anteriores del mismo tema (para contexto relevante).

    Si se pasa sesion_id, filtra SOLO turnos de esa sesión (evita que
    contexto de sesiones viejas con el mismo tema contamine la actual).
    Sin sesion_id, mantiene el comportamiento legacy (busca en todo el historial).
    """
    try:
        with _db() as con:
            if sesion_id:
                filas = con.execute("""
                    SELECT fecha, rol, texto, sesion_id
                    FROM conversaciones
                    WHERE tema = ? AND sesion_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (tema, sesion_id, limit)).fetchall()
            else:
                filas = con.execute("""
                    SELECT fecha, rol, texto, sesion_id
                    FROM conversaciones
                    WHERE tema = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (tema, limit)).fetchall()
            return [dict(f) for f in reversed(filas)]
    except Exception as e:
        print(f"[MEMORIA] Error buscando turnos por tema: {e}")
        return []

def obtener_turnos_por_sesion(sesion_id: str) -> list[dict]:
    """Recupera todos los turnos de una sesión específica."""
    try:
        with _db() as con:
            filas = con.execute("""
                SELECT fecha, rol, texto, tema
                FROM conversaciones
                WHERE sesion_id = ?
                ORDER BY id ASC
            """, (sesion_id,)).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        print(f"[MEMORIA] Error buscando sesión: {e}")
        return []


def obtener_ultimos_turnos(n: int = 5) -> list[dict]:
    """Últimos N turnos sin filtro (para debug o fallback)."""
    try:
        with _db() as con:
            filas = con.execute("""
                SELECT fecha, rol, texto, tema, sesion_id
                FROM conversaciones
                ORDER BY id DESC
                LIMIT ?
            """, (n,)).fetchall()
            return [dict(f) for f in reversed(filas)]
    except Exception as e:
        print(f"[MEMORIA] Error leyendo últimos turnos: {e}")
        return []


def listar_sesiones(limit: int = 15) -> list[dict]:
    """
    Resumen de las últimas `limit` sesiones distintas (para el comando
    '/sesiones' de la TUI): sesion_id, fecha de inicio, cantidad de turnos,
    y un preview del primer mensaje de usuario para identificarlas de un
    vistazo sin tener que abrirlas.
    """
    try:
        with _db() as con:
            filas = con.execute("""
                SELECT sesion_id, MIN(fecha) AS inicio, COUNT(*) AS turnos
                FROM conversaciones
                WHERE sesion_id != ''
                GROUP BY sesion_id
                ORDER BY inicio DESC
                LIMIT ?
            """, (limit,)).fetchall()

            resultado = []
            for f in filas:
                primer = con.execute("""
                    SELECT texto FROM conversaciones
                    WHERE sesion_id = ? AND rol = 'usuario'
                    ORDER BY id ASC LIMIT 1
                """, (f["sesion_id"],)).fetchone()
                resultado.append({
                    "sesion_id": f["sesion_id"],
                    "inicio":    f["inicio"],
                    "turnos":    f["turnos"],
                    "preview":   (primer["texto"][:60] if primer and primer["texto"] else ""),
                })
            return resultado
    except Exception as e:
        print(f"[MEMORIA] Error listando sesiones: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# ALIAS / COMPAT — para nodos del grafo que todavía esperan estas funcs
# ══════════════════════════════════════════════════════════════════════
def guardar_memoria(mem: dict) -> None:
    """
    Stub de compatibilidad.
    Algunos nodos viejos del grafo siguen importando `guardar_memoria`,
    pero ya no hace falta: cada escritura (registrar_turno / registrar_comando
    / actualizar_core / guardar_recuerdo) persiste al instante.
    """
    # Intencionalmente vacío: la persistencia ya es inmediata.
    return None


# ══════════════════════════════════════════════════════════════════════
# COMPATIBILIDAD LEGACY (para engine_bridge, backend, etc.)
# ══════════════════════════════════════════════════════════════════════
def inicializar_db() -> None:
    """Alias de compatibilidad. Llama a asegurar_esquema()."""
    asegurar_esquema()
