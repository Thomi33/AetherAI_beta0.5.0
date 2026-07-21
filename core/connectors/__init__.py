"""
core/connectors — Registro de conectores MCP de Aether.

Agregar un conector nuevo:
    1. Crear core/connectors/<nombre>.py
    2. Definir CONNECTOR = Connector(server="...", normalize=..., permissions={...})
    3. Nada más. El registry lo descubre solo (ver registry.py).

Sin tocar graph_nodes.py, tool_registry.py ni graph_builder.py.
"""

from core.connectors.base import Connector
from core.connectors.registry import (
    get_registry,
    get_connector,
    normalize_args,
    permission_for,
    reload_registry,
)

__all__ = [
    "Connector",
    "get_registry",
    "get_connector",
    "normalize_args",
    "permission_for",
    "reload_registry",
]
