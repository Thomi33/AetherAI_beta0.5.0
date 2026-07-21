"""
core/connectors/base.py — Contrato mínimo de un conector MCP.

Un conector NO reemplaza a MCPClientManager (core/tools/mcp_client.py):
ese sigue siendo el único que habla el protocolo MCP de verdad (conexión,
list_tools, call_tool). Un conector es solo un módulo de conocimiento
DECLARATIVO sobre un server MCP puntual, para sacar del núcleo (graph_nodes.py)
lo que hoy vive hardcodeado ahí (ej. la limpieza de queries de GitHub).

Agregar un conector nuevo = crear un archivo core/connectors/<server>.py que
defina una variable de módulo `CONNECTOR = Connector(...)`. El registry
(core/connectors/registry.py) lo descubre solo — no hay que tocar
graph_nodes.py, tool_registry.py ni graph_builder.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# Firma del hook de normalización: (tool_name, arguments, orden_usuario) -> arguments
NormalizeFn = Callable[[str, dict, str], dict]

# Niveles de permiso por tool:
#   "auto" — se ejecuta sin pedir confirmación (equivalente a como se
#            comportaba TODO hasta ahora, o sea el default histórico).
#   "ask"  — si modo_autonomo=False, se pide confirmación antes de llamar
#            la tool (mismo mecanismo que ya usan node_shell/node_codigo
#            vía _confirmar_usuario). Si modo_autonomo=True, se ejecuta
#            igual que "auto" (autonomía = no preguntar).
#   "deny" — nunca se ejecuta, sin importar modo_autonomo. Para tools que
#            todavía no querés habilitar aunque el server las exponga.
PermissionLevel = str  # "auto" | "ask" | "deny"


@dataclass
class Connector:
    """Metadata declarativa de un server MCP."""

    server: str
    """Nombre del server tal cual aparece en core/config/mcp_servers.json."""

    normalize: Optional[NormalizeFn] = None
    """
    Hook opcional para corregir/completar argumentos antes de llamar una
    tool de este server (ej. limpiar lenguaje natural en queries de GitHub
    Search, que no entiende "los repos más populares de X").
    Si es None, los argumentos pasan sin tocar.
    """

    permissions: dict[str, PermissionLevel] = field(default_factory=dict)
    """Nivel de permiso por nombre de tool. Lo que no está acá usa default_permission."""

    default_permission: PermissionLevel = "auto"
    """Nivel de permiso para tools de este server no listadas en `permissions`."""

    def normalize_args(self, tool: str, arguments: dict, orden: str) -> dict:
        if self.normalize is None:
            return arguments
        try:
            return self.normalize(tool, dict(arguments), orden)
        except Exception:
            # Un conector mal escrito no debe tumbar la llamada MCP entera.
            return arguments

    def permission_for(self, tool: str) -> PermissionLevel:
        return self.permissions.get(tool, self.default_permission)
