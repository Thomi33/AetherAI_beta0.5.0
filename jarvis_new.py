"""
Aether (Javier) - Wrapper de compatibilidad para la arquitectura refactorizada.

CAMBIO: orquestación migrada de CrewAI a LangGraph.
La única línea que cambia respecto al jarvis_new.py original es el import
de procesar_orden_completo: ahora viene de core.agent.graph en vez de
core.services.aether_service.

Todo lo demás (memoria, herramientas, parsing) se mantiene 100% igual,
porque esa lógica vive en módulos que el grafo reutiliza directamente.
"""
import time

from core.config.settings import *

from core.memory.memory_manager import (
    inicializar_db,
    cargar_memoria,
    guardar_memoria,
    registrar_turno,
    registrar_comando,
    obtener_ultimos_turnos,
)

from core.tools.shell_executor import ejecutar_comando
from core.tools.flatpak_manager import (
    actualizar_flatpaks,
    buscar_flatpak_en_memoria,
)
from core.tools.web_search import buscar_web
from core.tools.url_reader import leer_url
from core.tools.file_writer import escribir_archivo
from core.tools.vision import ver_pantalla

from core.parser.shell_parser import extraer_comando_shell
from core.parser.response_parser import analizar_salida

# ── CAMBIO CLAVE ──────────────────────────────────────────────
from core.agent.graph import procesar_orden_completo, get_grafo
# ────────────────────────────────────────────────────────────────
import traceback  # <-- solo esto aquí arriba, el except va ABAJO

def main():
    """Punto de entrada principal del sistema Aether."""
    inicializar_db()
    mem = cargar_memoria()

    print("\n🤖 [SISTEMA] Secuencia de inicio completada.")
    print(f"   📦 Flatpaks en memoria: {len(mem['flatpaks'])}")
    print(f"   💬 Turnos conversacionales recordados: {len(mem['conversacion'])}")

    modo   = "ACTIVO (ejecución autónoma)" if MODO_AUTONOMO else "MANUAL (requiere confirmación)"
    nombre = mem["preferencias"].get("nombre_usuario")
    print(f"🎙️  Aether: Buenos días, {nombre}. Matrices listas (LangGraph). Modo Autónomo: {modo}.\n")

    get_grafo()

    while True:
        try:
            orden = input("🧠 Creador: ").strip()
            if not orden:
                continue
            if orden.lower() in {"salir", "adios", "exit", "apágate", "quit"}:
                print("\n🤖 [SISTEMA] Desconectando sistemas. Hasta luego.")
                break

            procesar_orden_completo(orden, mem, modo_autonomo=MODO_AUTONOMO)
            print(f"\n{'─'*50}")

        except KeyboardInterrupt:
            print("\n\n🤖 [SISTEMA] Apagado limpio. Hasta luego.")
            break
        except Exception as e:
            print(f"\n❌ [ERROR CRÍTICO]: {e}")
            traceback.print_exc()  # <-- aquí dentro del except


if __name__ == "__main__":
    main()