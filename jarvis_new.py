"""
Aether (Javier) - Wrapper de compatibilidad para la arquitectura refactorizada.
Este archivo mantiene la interfaz pública original mientras delega al módulo core.
"""
import time

# Importar todas las configuraciones
from core.config.settings import *

# Importar funciones públicas de memoria
from core.memory.memory_manager import (
    inicializar_db,
    cargar_memoria,
    guardar_memoria,
    registrar_turno,
    registrar_comando,
    obtener_ultimos_turnos,
)

# Importar funciones públicas de herramientas
from core.tools.shell_executor import ejecutar_comando
from core.tools.flatpak_manager import (
    actualizar_flatpaks,
    buscar_flatpak_en_memoria,
)
from core.tools.web_search import buscar_web
from core.tools.url_reader import leer_url
from core.tools.file_writer import escribir_archivo
from core.tools.vision import ver_pantalla

# Importar funciones públicas de parsing
from core.parser.shell_parser import extraer_comando_shell
from core.parser.response_parser import analizar_salida

# Importar agent
from core.agent.builder import construir_agente

# Importar servicio principal
from core.services.aether_service import (
    _procesar_orden,
    procesar_orden_completo,
    _procesar_comando_memoria,
)


def main():
    """Punto de entrada principal del sistema Aether."""
    inicializar_db()
    mem = cargar_memoria()

    print("\n🤖 [SISTEMA] Secuencia de inicio completada.")
    print(f"   📦 Flatpaks en memoria: {len(mem['flatpaks'])}")
    print(f"   💬 Turnos conversacionales recordados: {len(mem['conversacion'])}")

    modo   = "ACTIVO (ejecución autónoma)" if MODO_AUTONOMO else "MANUAL (requiere confirmación)"
    nombre = mem["preferencias"].get("nombre_usuario")
    print(f"🎙️  Javier: Buenos días, {nombre}. Matrices listas. Modo Autónomo: {modo}.\n")

    while True:
        try:
            orden = input("🧠 Creador: ").strip()
            if not orden:
                continue
            if orden.lower() in {"salir", "adios", "exit", "apágate", "quit"}:
                print("\n🤖 [SISTEMA] Desconectando sistemas. Hasta luego.")
                break

            # Procesar orden completa
            procesar_orden_completo(orden, mem, modo_autonomo=MODO_AUTONOMO)
            print(f"\n{'─'*50}")

        except KeyboardInterrupt:
            print("\n\n🤖 [SISTEMA] Apagado limpio. Hasta luego.")
            break
        except Exception as e:
            print(f"\n❌ [ERROR CRÍTICO]: {e}")


if __name__ == "__main__":
    main()
