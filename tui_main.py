#!/usr/bin/env python3
"""
tui_main.py — Punto de entrada principal de Aether (recomendado).

Uso:
    cd /home/thomi/mi_proyecto_crew
    source env/bin/activate
    python tui_main.py

Este es el camino moderno y estable. 
- Siempre usa razonamiento del modelo (Ornith) para decidir tool (incluyendo launch para programas/flatpaks/juegos).
- Sin modo legacy.
- Soporte mejorado para launch (flatpak apps como Sober).
"""
import sys
import uuid
import traceback
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.memory.memory_manager import (
    asegurar_esquema,
    cargar_memoria,
    normalizar_mem,
)
from core.services.graph_service import procesar_orden_grafo


def main():
    # 1. Asegurar que las tablas existen en la DB correcta (current.db).
    #    ANTES esto creaba la tabla en ~/mi_proyecto_crew/memoria.db,
    #    pero memory_manager leía de /mnt/basurero/Javier/db/memoria.db →
    #    dos DBs distintas → "no such table: core_memory" siempre.
    asegurar_esquema()

    # 2. Cargar memoria
    mem = normalizar_mem(cargar_memoria())

    # 3. Generar sesión única por ejecución
    sesion_id = uuid.uuid4().hex
    mem["sesion_id"] = sesion_id
    print(f"🔑 Sesión: {sesion_id[:8]}...")
    print("🤖 Aether listo. Escribe 'salir' para terminar.\n")

    while True:
        try:
            orden = input("🧠 Creador: ").strip()
            if orden.lower() in ("salir", "exit", "adios", "quit"):
                break
            if not orden:
                continue

            t0 = time.perf_counter()
            respuesta = procesar_orden_grafo(
                orden, mem, modo_autonomo=True
            )
            elapsed = time.perf_counter() - t0
            print(f"🎙️  Aether: {respuesta}\n")
            print(f"⏱️  {elapsed:.2f}s\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            # Si el error sigue siendo "cannot import name 'guardar_memoria'"
            # el problema está en graph_nodes.py / node_finalize / graph_service.py,
            # no acá. Ejecutar:  grep -rn "guardar_memoria" core/
            print(f"❌ Error: {e}")
            traceback.print_exc()

    print("👋 Hasta luego.")


if __name__ == "__main__":
    main()