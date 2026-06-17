"""
Aether CLI - Interfaz de terminal para el agente Aether
Loop interactivo que usa AetherService para procesar órdenes
"""

import sys
import os
import logging
from pathlib import Path

# Agregar ruta del proyecto al path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.aether_service import AetherService

# =====================================================================
# CONFIGURACIÓN DE LOGGING
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger("aether_cli")


# =====================================================================
# FUNCIÓN PRINCIPAL DEL CLI
# =====================================================================
def main():
    """Loop interactivo del CLI de Aether"""
    
    print("\n" + "="*60)
    print("🤖 AETHER - Agente Local Inteligente (CLI)")
    print("="*60)
    
    try:
        # Inicializar Aether
        logger.info("Inicializando Aether...")
        print("\n⏳ Cargando agente...")
        AetherService.initialize()
        logger.info("✅ Aether inicializado correctamente")
        print("✅ Aether listo\n")
        
    except Exception as e:
        logger.error(f"❌ Error al inicializar Aether: {e}", exc_info=True)
        print(f"❌ Error al inicializar: {e}")
        sys.exit(1)
    
    # Loop interactivo
    print("-" * 60)
    print("Escribe 'salir' o 'exit' para terminar la sesión")
    print("-" * 60 + "\n")
    
    while True:
        try:
            # Capturar entrada del usuario
            user_input = input("🧠 Tú: ").strip()
            
            if not user_input:
                continue
            
            # Comandos de salida
            if user_input.lower() in {"salir", "adios", "exit", "apágate", "quit"}:
                print("\n🤖 [SISTEMA] Desconectando sistemas. Hasta luego.\n")
                break
            
            # Procesar mensaje
            logger.debug(f"Procesando: {user_input[:50]}...")
            print("\n🤖 [Aether PROCESANDO...]")
            
            result = AetherService.process_message(user_input)
            response = result.get("response", "Sin respuesta")
            status = result.get("agent_status", "unknown")
            
            if status == "error":
                print(f"❌ Error: {response}")
            else:
                print(f"\n🎙️  Aether: {response}")
            
            print("\n" + "-" * 60 + "\n")
            
        except KeyboardInterrupt:
            print("\n\n🛑 Sesión interrumpida por el usuario.")
            break
        except Exception as e:
            logger.error(f"Error en el loop principal: {e}", exc_info=True)
            print(f"❌ Error: {e}")
            print("-" * 60 + "\n")


if __name__ == "__main__":
    main()
