"""
core/connectors/github.py — Conector para el server MCP "github".

Migrado desde la lógica que antes vivía hardcodeada en
core/agent/graph_nodes.py::_normalizar_args_mcp (if server == "github": ...).
Comportamiento idéntico al anterior, solo movido de lugar.
"""

from core.connectors.base import Connector

_RUIDO_QUERY_GITHUB = (
    "most popular", "most famous", "más populares", "populares",
    "más famosos", "más famosas", "famosos", "famosas", "famoso", "famosa",
    "the", "los", "las", "repositorios", "repositories", "repos", "repo",
    "de", "sobre", "en github", "github",
)


def _normalizar_search_repositories(tool: str, arguments: dict, orden: str) -> dict:
    if tool != "search_repositories":
        return arguments

    query = (arguments.get("query") or "").strip().lower()
    for kw in _RUIDO_QUERY_GITHUB:
        query = query.replace(kw, " ")
    query = " ".join(query.split()).strip()

    # GitHub Search permite buscar SOLO con qualifiers (ej. stars:>1000)
    # si no queda tema tras limpiar el ruido conversacional.
    if "stars:" not in query:
        query = f"{query} stars:>1000".strip()

    arguments["query"] = query
    arguments.setdefault("sort", "stars")
    arguments.setdefault("order", "desc")
    return arguments


CONNECTOR = Connector(
    server="github",
    normalize=_normalizar_search_repositories,
    permissions={
        # Lecturas: sin gate.
        "search_repositories": "auto",
        "search_code": "auto",
        "search_issues": "auto",
        "get_file_contents": "auto",
        "list_commits": "auto",
        "list_issues": "auto",
        "list_pull_requests": "auto",
        # Escrituras: pedir confirmación cuando modo_autonomo=False.
        "create_issue": "ask",
        "create_pull_request": "ask",
        "create_or_update_file": "ask",
        "merge_pull_request": "ask",
        "push_files": "ask",
        "delete_file": "ask",
    },
    default_permission="ask",  # tool desconocida del server → mejor preguntar
)
