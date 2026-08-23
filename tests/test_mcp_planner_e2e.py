"""El planner debe elegir MCP por capacidad, sin depender de un server local."""

from unittest.mock import MagicMock

import core.agent.graph_nodes as gn
import core.tools.mcp_client as mcp_client
from core.agent.graph_state import crear_estado_inicial


def test_mcp_planner_infiere_args():
    manager = MagicMock()
    manager.list_all_tools.return_value = {
        "server_filesystem": [{
            "name": "list_directory",
            "description": "Lista los directorios permitidos",
            "input_schema": {"type": "object", "required": ["path"]},
        }],
    }

    original_manager = mcp_client.get_mcp_manager
    original_llm = gn._llm_chat
    mcp_client.get_mcp_manager = lambda: manager
    gn._llm_chat = lambda **_: '{"server":"server_filesystem","name":"list_directory","arguments":{"path":"/tmp"}}'
    try:
        resultado = gn.node_planner(crear_estado_inicial("listame directorios usando MCP", {}))
    finally:
        mcp_client.get_mcp_manager = original_manager
        gn._llm_chat = original_llm

    paso = resultado["plan_pasos"][0]
    assert paso["tool"] == "mcp"
    assert paso["args"] == {
        "server": "server_filesystem",
        "name": "list_directory",
        "arguments": {"path": "/tmp"},
    }


def test_github_prefiere_mcp_sobre_keyword_web():
    manager = MagicMock()
    manager.list_all_tools.return_value = {
        "github": [{
            "name": "search_repositories",
            "description": "Busca repositorios",
            "input_schema": {"type": "object", "required": ["query"]},
        }],
    }
    original_manager = mcp_client.get_mcp_manager
    original_llm = gn._llm_chat
    mcp_client.get_mcp_manager = lambda: manager
    gn._llm_chat = lambda **_: '{"server":"github","name":"search_repositories","arguments":{"query":"aether"}}'
    try:
        resultado = gn.node_planner(crear_estado_inicial("buscá repositorios de Aether en GitHub", {}))
    finally:
        mcp_client.get_mcp_manager = original_manager
        gn._llm_chat = original_llm

    assert resultado["plan_pasos"][0]["tool"] == "mcp"
    assert resultado["plan_pasos"][0]["args"]["server"] == "github"
