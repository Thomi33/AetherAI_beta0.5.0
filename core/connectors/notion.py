"""
core/connectors/notion.py — Conector para el server MCP "notion".

Sin normalización de args por ahora (la inferencia genérica vía LLM en
_inferir_args_mcp ya cubre el caso de uso actual: "API-post-search").
Se registra igual para declarar permisos: Notion permite crear/editar
páginas, así que cualquier tool que no sea búsqueda/lectura pide
confirmación por defecto.
"""

from core.connectors.base import Connector

CONNECTOR = Connector(
    server="notion",
    permissions={
        "API-post-search": "auto",
        "API-retrieve-a-page": "auto",
        "API-get-block-children": "auto",
        "API-retrieve-a-database": "auto",
        "API-query-a-database": "auto",
        "API-post-page": "ask",
        "API-patch-page": "ask",
        "API-patch-block-children": "ask",
        "API-delete-a-block": "ask",
    },
    default_permission="ask",
)
