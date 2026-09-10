"""
Aether — Punto de entrada alternativo (loop propio sobre el grafo).
Acepta --workdir RUTA (lo pasa bin/aether); si no, usa $AETHER_CWD o cwd.
"""
import os
import sys
import traceback

for _i, _a in enumerate(sys.argv):
    if _a == "--workdir" and _i + 1 < len(sys.argv):
        os.environ["AETHER_CWD"] = sys.argv[_i + 1]
        break
    if _a.startswith("--workdir="):
        os.environ["AETHER_CWD"] = _a.split("=", 1)[1]
        break

from core.config.settings import MODO_AUTONOMO, MODELO
from core.memory.memory_manager import (
    cargar_memoria,
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
from core.agent.graph_builder import get_graph
from core.services.graph_service import procesar_orden_grafo


def procesar_orden_completo(orden: str, mem: dict, modo_autonomo: bool = True) -> str:
    return procesar_orden_grafo(orden, mem, modo_autonomo)


def main():
    from core.config.dir_authorization import resolver_dir_trabajo, fue_evaluada, presentacion_y_confirmacion
    ruta = resolver_dir_trabajo()
    if not fue_evaluada(ruta):
        presentacion_y_confirmacion(ruta)
    print(f"\n📁 Dir. trabajo: {ruta}")
    mem = cargar_memoria()
    core         = mem.get("core", {})
    conversacion = mem.get("conversacion", [])

    print("\n🤖 [SISTEMA] Secuencia de inicio completada.")
    print(f"   💬 Turnos conversacionales recordados: {len(conversacion)}")

    modo   = "ACTIVO (ejecución autónoma)" if MODO_AUTONOMO else "MANUAL (requiere confirmación)"
    nombre = core.get("usuario", "Creador")
    print(f"🎙️  Aether: Buenos días, {nombre}. Matrices listas (LangGraph). Modo Autónomo: {modo}.\n")

    # Precalentar el grafo
    get_graph()

    while True:
        try:
            orden = input("🧠 Creador: ").strip()
            if not orden:
                continue
            if orden.lower() in {"salir", "adios", "exit", "apágate", "quit"}:
                print("\n🤖 [SISTEMA] Desconectando sistemas. Hasta luego.")
                break

            procesar_orden_completo(orden, mem, modo_autonomo=MODO_AUTONOMO)
            print(f"\n{'─' * 50}")

        except (EOFError, KeyboardInterrupt):
            print("\n\n🤖 [SISTEMA] Apagado limpio. Hasta luego.")
            break
        except Exception as e:
            print(f"\n❌ [ERROR CRÍTICO]: {e}")
            traceback.print_exc()


# ANTES decía:  if name == "main":   ← sin underscores, nunca corría.
if __name__ == "__main__":
    main()