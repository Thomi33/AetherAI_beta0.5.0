"""
AetherService: Adaptador singleton para integración de Aether con FastAPI.
Utiliza la arquitectura refactorizada en core/.
- Inicializa UNA SOLA VEZ
- Mantiene memoria en RAM entre requests
- Protege SQLite con threading.Lock
- Registra errores sin exponerlos al frontend
"""

import logging
import threading
import sys
from pathlib import Path
from typing import Dict, Any

# =====================================================================
# CONFIGURACIÓN DE LOGGING
# =====================================================================
logger = logging.getLogger("aether_service")
logger.setLevel(logging.DEBUG)

# =====================================================================
# SINGLETON + LOCK PARA THREAD-SAFETY
# =====================================================================
_AETHER_LOCK = threading.Lock()
_AETHER_INITIALIZED = False
_AETHER_MEMORY: Dict[str, Any] = None


class AetherService:
    """Servicio singleton para ejecutar el agente Aether desde FastAPI."""

    @staticmethod
    def _setup_paths():
        """Agrega ruta del proyecto a sys.path."""
        project_root = Path(__file__).parent.parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

    @staticmethod
    def initialize():
        """Inicializa el agente Aether UNA SOLA VEZ."""
        global _AETHER_INITIALIZED, _AETHER_MEMORY
        
        if _AETHER_INITIALIZED:
            logger.debug("Aether ya inicializado, reutilizando instancia")
            return
        
        with _AETHER_LOCK:
            # Double-check después de adquirir lock
            if _AETHER_INITIALIZED:
                return
            
            try:
                # Setup paths
                AetherService._setup_paths()
                
                # Importar módulos desde arquitectura refactorizada
                from core.memory.memory_manager import inicializar_db, cargar_memoria
                from core.tools.flatpak_manager import actualizar_flatpaks
                
                # Inicializar DB (idempotente)
                logger.debug("Inicializando BD de Aether...")
                inicializar_db()
                
                # Cargar memoria desde DB
                logger.debug("Cargando memoria desde BD...")
                _AETHER_MEMORY = cargar_memoria()
                
                # Actualizar índice de programas
                logger.debug("Indexando programas del sistema...")
                actualizar_flatpaks(_AETHER_MEMORY, salida_lista="")
                
                _AETHER_INITIALIZED = True
                logger.info("✅ Aether inicializado correctamente")
            
            except Exception as e:
                logger.error(f"❌ Error durante inicialización de Aether: {e}", exc_info=True)
                _AETHER_INITIALIZED = False
                raise

    @staticmethod
    def process_message(user_message: str) -> Dict[str, Any]:
        """
        Procesa un mensaje del usuario y devuelve respuesta del agente.
        
        Thread-safe: usa Lock para proteger acceso a BD y memoria.
        """
        # Inicializar si es necesario
        if not _AETHER_INITIALIZED:
            try:
                AetherService.initialize()
            except Exception as e:
                logger.error(f"Fallo al inicializar Aether: {e}")
                return {
                    "response": "Aether no pudo inicializarse",
                    "agent_status": "error"
                }
        
        try:
            # Importar módulos necesarios
            from core.memory.memory_manager import registrar_turno
            from core.services.aether_service import _procesar_orden
            
            with _AETHER_LOCK:
                logger.debug(f"Procesando mensaje: {user_message[:50]}...")
                
                # Registrar entrada del usuario
                registrar_turno(_AETHER_MEMORY, "usuario", user_message)
                
                # Procesar la orden
                respuesta = _procesar_orden(user_message, _AETHER_MEMORY)
                
                # Registrar respuesta del agente
                registrar_turno(_AETHER_MEMORY, "jarvis", respuesta)
                
                logger.debug(f"Respuesta generada: {respuesta[:50]}...")
            
            return {
                "response": respuesta,
                "agent_status": "ready"
            }
        
        except Exception as e:
            logger.error(f"Error procesando mensaje: {e}", exc_info=True)
            return {
                "response": "Aether encontró un error interno",
                "agent_status": "error"
            }

    @staticmethod
    def get_status() -> Dict[str, Any]:
        """Obtiene estado del agente."""
        try:
            if not _AETHER_INITIALIZED:
                return {
                    "name": "Aether",
                    "status": "uninitialized",
                    "agent_status": "offline"
                }
            
            return {
                "name": "Aether",
                "status": "ready",
                "model": "qwen3.5:9b",
                "version": "2.0.0-refactored",
                "memory_size": len(_AETHER_MEMORY.get("conversacion", [])) if _AETHER_MEMORY else 0,
                "agent_status": "online"
            }
        except Exception as e:
            logger.error(f"Error obteniendo status: {e}")
            return {
                "name": "Aether",
                "status": "error",
                "agent_status": "offline"
            }
