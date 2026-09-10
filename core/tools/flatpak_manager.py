"""
Gestor de aplicaciones Flatpak.
"""
import re

from core.memory.memory_manager import guardar_memoria


def actualizar_flatpaks(mem: dict, salida_lista: str) -> None:
    """
    Parsea la salida de 'flatpak list' y actualiza caché en memoria.
    Almacena varias claves para lookup rápido pero solo nombres/ID reales.
    """
    salida_lista = "" if salida_lista is None else str(salida_lista)
    if not salida_lista.strip():
        return
    mem.setdefault("flatpaks", {})

    for linea in salida_lista.strip().splitlines():
        partes = linea.split(None, 1)
        if len(partes) == 2:
            app_id, nombre = partes[0].strip(), partes[1].strip()
            if app_id.startswith(("com.", "org.", "io.", "net.", "app.")):
                mem["flatpaks"][nombre.lower()] = app_id
                mem["flatpaks"][app_id.lower()] = app_id
                last = app_id.split(".")[-1].lower()
                if last:
                    mem["flatpaks"][last] = app_id
    guardar_memoria(mem)


def buscar_flatpak_en_memoria(mem: dict, orden: str) -> str | None:
    """
    Busca un flatpak en memoria caché por nombre en la orden.
    Más estricto para evitar colisiones tipo "launcher".
    """
    orden_lower = orden.lower()
    GENERIC = {"launcher", "app", "client"}
    parts = [p for p in re.findall(r"[a-z0-9]{4,}", orden_lower) if p not in GENERIC]
    for nombre, app_id in (mem.get("flatpaks") or {}).items():
        nl = nombre.lower()
        # require that a distinctive part from order is in the stored key, or direct ID match
        if any(p in nl for p in parts) or nl in orden_lower:
            return app_id
        # fallback exact last seg
        last = app_id.split(".")[-1].lower() if "." in app_id else ""
        if last and last in orden_lower:
            return app_id
    return None


def intentar_lanzar_flatpak(mem: dict, salida_lista: str, orden: str) -> None:
    """
    Intenta encontrar y lanzar un flatpak de acuerdo con la orden.
    Actualiza caché y ejecuta si encuentra coincidencia.
    """
    from core.tools.shell_executor import ejecutar_comando
    
    actualizar_flatpaks(mem, salida_lista)
    orden_lower = orden.lower()
    for linea in salida_lista.strip().splitlines():
        partes = linea.split(None, 1)
        if len(partes) < 2:
            continue
        app_id, nombre = partes[0].strip(), partes[1].strip()
        segmento = app_id.lower().split(".")[-1]
        if nombre.lower() in orden_lower or segmento in orden_lower:
            print(f"\n🚀 [Aether]: ID encontrado → {app_id}. Lanzando ahora...")
            salida, _ = ejecutar_comando(f"flatpak run {app_id}")
            print(salida)
            return
