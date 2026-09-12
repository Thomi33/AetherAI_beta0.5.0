"""Configuración global de pytest para mi_proyecto_crew.

Agrega el directorio raíz al sys.path para que los módulos bajo `core/`
sean importables sin instalación del paquete.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
