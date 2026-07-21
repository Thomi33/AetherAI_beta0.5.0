"""
core/connectors/registry.py — Descubrimiento y acceso a conectores.

Escanea core/connectors/*.py buscando módulos con una variable `CONNECTOR`
(instancia de core.connectors.base.Connector) y los indexa por nombre de
server. No importa nada de core.agent ni core.tools — es una capa aislada
que node_mcp consulta, no al revés (evita imports circulares).

Uso típico (desde graph_nodes.py / node_mcp):

    from core.connectors import normalize_args, permission_for

    arguments = normalize_args(server, name, arguments, orden)
    nivel = permission_for(server, name)  # "auto" | "ask" | "deny"
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Optional

from core.connectors.base import Connector, PermissionLevel

_MODULOS_INTERNOS = {"base", "registry", "__init__"}

_registro: dict[str, Connector] | None = None


def _descubrir_conectores() -> dict[str, Connector]:
    """
    Importa todos los módulos de core.connectors (salvo los internos) y
    recolecta su `CONNECTOR`. Un conector con error de import se loguea y
    se saltea — nunca debe romper el arranque de Aether.
    """
    import core.connectors as paquete

    registro: dict[str, Connector] = {}
    for _, nombre_modulo, es_paquete in pkgutil.iter_modules(paquete.__path__):
        if es_paquete or nombre_modulo in _MODULOS_INTERNOS:
            continue
        try:
            mod = importlib.import_module(f"core.connectors.{nombre_modulo}")
        except Exception as e:
            print(f"⚠️  [CONNECTORS]: no se pudo cargar '{nombre_modulo}': {e}")
            continue

        conector = getattr(mod, "CONNECTOR", None)
        if conector is None:
            continue
        if not isinstance(conector, Connector):
            print(f"⚠️  [CONNECTORS]: '{nombre_modulo}'.CONNECTOR no es un Connector válido, ignorado.")
            continue

        registro[conector.server] = conector

    return registro


def get_registry() -> dict[str, Connector]:
    """Registro completo {server: Connector}, cacheado tras el primer descubrimiento."""
    global _registro
    if _registro is None:
        _registro = _descubrir_conectores()
    return _registro


def reload_registry() -> None:
    """Fuerza un re-descubrimiento (útil en tests o edición en caliente)."""
    global _registro
    _registro = None


def get_connector(server: str) -> Optional[Connector]:
    return get_registry().get(server)


def normalize_args(server: str, tool: str, arguments: dict, orden: str) -> dict:
    """
    Normaliza args vía el conector del server si existe. Si el server no
    tiene conector registrado (aún no migrado, o server genérico sin
    particularidades), retorna los args sin tocar — comportamiento
    idéntico al actual.
    """
    conector = get_connector(server)
    if conector is None:
        return arguments
    return conector.normalize_args(tool, arguments, orden)


def permission_for(server: str, tool: str) -> PermissionLevel:
    """
    Nivel de permiso para tool/server. Server sin conector registrado →
    "auto" (mismo comportamiento que hoy, sin gate adicional).
    """
    conector = get_connector(server)
    if conector is None:
        return "auto"
    return conector.permission_for(tool)
