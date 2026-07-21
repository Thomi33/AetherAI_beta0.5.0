"""
core/connectors/postgres.py — Conector para el server MCP "postgres".

NOTA: core/config/mcp_servers.json todavía tiene una connection string de
ejemplo ("usuario:password@localhost..."), así que este server no está
realmente activo todavía. Se registra igual para que, el día que lo
conectes a una DB real, el default sea conservador: TODO pide confirmación
si modo_autonomo=False. No hay ninguna tool de este server marcada "auto" a
propósito — el fallback heurístico de graph_nodes.py hoy pone
"SELECT 1" como placeholder si no puede inferir el SQL real, y no es una
garantía suficiente de que la próxima query inferida sea inofensiva.
"""

from core.connectors.base import Connector

CONNECTOR = Connector(
    server="postgres",
    permissions={},
    default_permission="ask",
)
