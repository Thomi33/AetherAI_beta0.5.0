"""
Gestor de memoria persistente y volátil.
"""
import json
from datetime import datetime

from core.config.settings import MAX_HISTORIAL
from core.memory.sqlite_db import get_db_connection, inicializar_db


def _memoria_vacia() -> dict:
    """Retorna estructura de memoria vacía con valores por defecto."""
    return {
        "preferencias": {
            "nombre_usuario": "Thomas",
            "navegador":      "brave",
            "notas":          [],
        },
        "flatpaks":           {},
        "historial_comandos": [],
        "conversacion":       [],
    }


def normalizar_mem(mem: dict | None) -> dict:
    """
    Garantiza que `mem` cumpla el esquema obligatorio de memoria de Aether.

    Rellena de forma NO destructiva cualquier clave/sub-clave faltante con
    los valores por defecto. Esto evita KeyError en nodos y en el
    constructor de contexto cuando se recibe un `mem` parcial o vacío
    (p.ej. en tests o integraciones externas).

    Esquema garantizado:
        preferencias: dict con nombre_usuario, navegador, notas (list)
        flatpaks: dict
        historial_comandos: list
        conversacion: list

    Muta y retorna el mismo dict para conveniencia. Si `mem` es None,
    retorna una estructura vacía nueva.
    """
    base = _memoria_vacia()

    if not isinstance(mem, dict):
        return base

    # ── preferencias (dict anidado) ──────────────────────────────────
    prefs = mem.get("preferencias")
    if not isinstance(prefs, dict):
        prefs = {}
    for clave, valor_def in base["preferencias"].items():
        if clave not in prefs or prefs[clave] is None:
            prefs[clave] = valor_def
    # 'notas' debe ser siempre lista
    if not isinstance(prefs.get("notas"), list):
        prefs["notas"] = []
    mem["preferencias"] = prefs

    # ── flatpaks (dict) ──────────────────────────────────────────────
    if not isinstance(mem.get("flatpaks"), dict):
        mem["flatpaks"] = {}

    # ── historial_comandos (list) ────────────────────────────────────
    if not isinstance(mem.get("historial_comandos"), list):
        mem["historial_comandos"] = []

    # ── conversacion (list) ──────────────────────────────────────────
    if not isinstance(mem.get("conversacion"), list):
        mem["conversacion"] = []

    return mem


def cargar_memoria() -> dict:
    """
    Carga estado en memoria RAM desde DB + defaults.
    Recupera preferencias, historial de comandos y conversaciones recientes.
    """
    inicializar_db()
    mem = _memoria_vacia()
    
    try:
        con = get_db_connection()
        
        # Preferencias guardadas
        filas = con.execute(
            "SELECT contenido FROM recuerdos WHERE categoria='preferencias' ORDER BY id DESC LIMIT 1"
        ).fetchall()
        if filas:
            prefs = json.loads(filas[0]["contenido"])
            mem["preferencias"].update(prefs)

        # Historial de comandos recientes
        cmds = con.execute(
            "SELECT orden, cmd, fecha FROM comandos ORDER BY id DESC LIMIT 50"
        ).fetchall()
        mem["historial_comandos"] = [
            {"orden": r["orden"], "cmd": r["cmd"], "fecha": r["fecha"]}
            for r in reversed(cmds)
        ]

        # Últimos turnos conversacionales
        turnos = con.execute(
            "SELECT rol, texto, fecha FROM conversaciones ORDER BY id DESC LIMIT 100"
        ).fetchall()
        mem["conversacion"] = [
            {"rol": r["rol"], "texto": r["texto"], "fecha": r["fecha"]}
            for r in reversed(turnos)
        ]
        
        con.close()
    except Exception:
        pass
    
    return normalizar_mem(mem)


def guardar_preferencias(mem: dict) -> None:
    """Persiste preferencias en tabla recuerdos."""
    try:
        con = get_db_connection()
        con.execute(
            "INSERT INTO recuerdos (fecha, categoria, contenido, importancia) VALUES (?,?,?,?)",
            (
                datetime.now().isoformat(),
                "preferencias",
                json.dumps(mem["preferencias"], ensure_ascii=False),
                10
            )
        )
        con.commit()
        con.close()
    except Exception:
        pass


def guardar_memoria(mem: dict) -> None:
    """Wrapper de compatibilidad que persiste preferencias."""
    guardar_preferencias(mem)


def registrar_turno_db(rol: str, texto: str) -> None:
    """Registra turno conversacional en DB."""
    con = get_db_connection()
    con.execute(
        "INSERT INTO conversaciones (fecha, rol, texto) VALUES (?, ?, ?)",
        (datetime.now().isoformat(), rol, texto[:800])
    )
    con.commit()
    con.close()
    print(f"DEBUG SQLITE: Guardando turno en DB | Rol: {rol} | Texto: {texto[:50]}...")


def registrar_turno(mem: dict, rol: str, texto: str) -> None:
    """Registra turno en DB y en memoria RAM."""
    registrar_turno_db(rol, texto)
    mem["conversacion"].append({
        "rol":   rol,
        "texto": texto[:800],
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    mem["conversacion"] = mem["conversacion"][-MAX_HISTORIAL:]


def registrar_comando_db(orden: str, cmd: str) -> None:
    """Registra comando ejecutado en DB."""
    con = get_db_connection()
    con.execute(
        "INSERT INTO comandos (fecha, orden, cmd) VALUES (?, ?, ?)",
        (datetime.now().isoformat(), orden, cmd),
    )
    con.commit()
    con.close()
    print(f"DEBUG SQLITE CMD: {cmd}")


def registrar_comando(mem: dict, orden: str, cmd: str) -> None:
    """Registra comando en DB y en memoria RAM."""
    registrar_comando_db(orden, cmd)
    entrada = {"orden": orden, "cmd": cmd, "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")}
    mem["historial_comandos"].append(entrada)
    mem["historial_comandos"] = mem["historial_comandos"][-50:]


def obtener_ultimos_turnos(n: int = 20) -> list:
    """Retrieves last n conversation turns from DB."""
    try:
        con = get_db_connection()
        filas = con.execute(
            "SELECT rol, texto FROM conversaciones ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        con.close()
        return list(reversed(filas))
    except Exception:
        return []

# memory_manager.py — agregar esta función

def obtener_recuerdos(
    categoria: str | None = None,
    importancia_min: int = 0,
    limit: int = 20,
) -> list[dict]:
    """
    Lee de la tabla 'recuerdos' con filtros. Reemplaza el SELECT
    hardcodeado a categoria='preferencias' que ignoraba todo lo demás.
    """
    try:
        con = get_db_connection()
        sql = "SELECT categoria, contenido, importancia, fecha FROM recuerdos WHERE importancia >= ?"
        params = [importancia_min]
        if categoria:
            sql += " AND categoria = ?"
            params.append(categoria)
        sql += " ORDER BY importancia DESC, id DESC LIMIT ?"
        params.append(limit)

        filas = con.execute(sql, params).fetchall()
        con.close()
        return [dict(f) for f in filas]
    except Exception:
        return []
