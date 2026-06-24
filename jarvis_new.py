"""
Aether (Javier) - Punto de entrada principal.

CAMBIO vs versión anterior:
- orquestación migrada de CrewAI → LangGraph (graph_builder / graph_nodes)
- procesar_orden_completo() definido aquí: registra turno usuario,
  invoca el grafo y retorna la respuesta final
- get_grafo() renombrado a get_graph() (era inconsistente con graph_builder.py)
"""
import traceback

from core.config.settings import MODO_AUTONOMO, MODELO

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

# ── CAMBIO CLAVE: grafo LangGraph en vez de CrewAI ───────────────────
from core.agent.graph_builder import get_graph
from core.services.graph_service import procesar_orden_grafo


def procesar_orden_completo(orden: str, mem: dict, modo_autonomo: bool = True) -> str:
    """
    Procesa la orden a través del motor LangGraph.

    Delega en core.services.graph_service.procesar_orden_grafo() para mantener
    una única implementación del motor (registra el turno del usuario e invoca
    el grafo; node_finalize registra el turno de Aether).
    """
    return procesar_orden_grafo(orden, mem, modo_autonomo)


def main():
    """Punto de entrada principal del sistema Aether."""
    inicializar_db()
    mem = cargar_memoria()

    print("\n🤖 [SISTEMA] Secuencia de inicio completada.")
    print(f"   📦 Flatpaks en memoria: {len(mem['flatpaks'])}")
    print(f"   💬 Turnos conversacionales recordados: {len(mem['conversacion'])}")

    modo   = "ACTIVO (ejecución autónoma)" if MODO_AUTONOMO else "MANUAL (requiere confirmación)"
    nombre = mem["preferencias"].get("nombre_usuario", "Creador")
    print(f"🎙️  Aether: Buenos días, {nombre}. Matrices listas (LangGraph). Modo Autónomo: {modo}.\n")

    # Precalentar el grafo en el inicio (evita delay en la primera orden)
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
            print(f"\n{'─'*50}")

        except KeyboardInterrupt:
            print("\n\n🤖 [SISTEMA] Apagado limpio. Hasta luego.")
            break
        except Exception as e:
            print(f"\n❌ [ERROR CRÍTICO]: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()