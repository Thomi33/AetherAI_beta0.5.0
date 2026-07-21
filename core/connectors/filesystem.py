"""
core/connectors/filesystem.py — Conector para el server MCP
"server_filesystem" (@modelcontextprotocol/server-filesystem).

Sin normalización de args (los paths los infiere el LLM o el fallback
heurístico en _inferir_args_mcp). Declara permisos: lecturas libres,
escrituras/moves piden confirmación en modo no autónomo.
"""

from core.connectors.base import Connector

CONNECTOR = Connector(
    server="server_filesystem",
    permissions={
        "read_file": "auto",
        "read_text_file": "auto",
        "read_multiple_files": "auto",
        "list_directory": "auto",
        "list_directory_with_sizes": "auto",
        "directory_tree": "auto",
        "search_files": "auto",
        "get_file_info": "auto",
        "list_allowed_directories": "auto",
        "write_file": "ask",
        "edit_file": "ask",
        "create_directory": "ask",
        "move_file": "ask",
    },
    default_permission="ask",
)
