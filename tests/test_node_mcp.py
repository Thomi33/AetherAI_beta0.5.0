"""
Test de node_mcp SIN depender de un server MCP real conectado.

Verifica:
1. Caso feliz: args completos -> llama manager.call_tool() y guarda
   el resultado en mcp_result.
2. Caso error: faltan 'server'/'name' en args -> error_activo=True,
   sin reventar.
3. Que node_mcp lee sus PROPIOS args del paso (plan_index sin offset),
   igual que confirmamos con test_plan_index.py para shell.

Uso:
    cp test_node_mcp.py /home/thomi/mi_proyecto_crew/
    cd /home/thomi/mi_proyecto_crew
    python test_node_mcp.py
"""

import sys
from unittest.mock import MagicMock

sys.path.insert(0, "/home/thomi/mi_proyecto_crew")

from core.agent import graph_nodes as gn
from core.agent.graph_state import crear_estado_inicial
import core.tools.mcp_client as mcp_client_module


def test_caso_feliz():
    print("\n" + "=" * 70)
    print("TEST 1: caso feliz (args completos)")
    print("=" * 70)

    mock_manager = MagicMock()
    mock_manager.call_tool.return_value = "resultado simulado del server MCP"

    original_get_manager = mcp_client_module.get_mcp_manager
    mcp_client_module.get_mcp_manager = lambda: mock_manager

    try:
        state = crear_estado_inicial(orden="usa mcp para buscar algo", mem={}, modo_autonomo=True)
        state["plan_pasos"] = [
            {"tool": "mcp", "instruccion": "consultar notion",
             "args": {"server": "notion", "name": "search", "arguments": {"query": "roadmap"}}}
        ]
        state["plan_index"] = 0

        resultado = gn.node_mcp(state)

        assert resultado.get("mcp_result") == "resultado simulado del server MCP", resultado
        assert not resultado.get("error_activo"), resultado
        mock_manager.call_tool.assert_called_once_with("notion", "search", {"query": "roadmap"})

        print("✅ node_mcp llamó call_tool con server/name/arguments correctos.")
        print(f"   mcp_result = {resultado.get('mcp_result')!r}")
    finally:
        mcp_client_module.get_mcp_manager = original_get_manager


def test_caso_args_incompletos():
    print("\n" + "=" * 70)
    print("TEST 2: args incompletos (falta 'name')")
    print("=" * 70)

    state = crear_estado_inicial(orden="usa mcp", mem={}, modo_autonomo=True)
    state["plan_pasos"] = [
        {"tool": "mcp", "instruccion": "consultar algo",
         "args": {"server": "notion"}}  # falta 'name'
    ]
    state["plan_index"] = 0

    resultado = gn.node_mcp(state)

    assert resultado.get("error_activo") is True, resultado
    assert "mcp" == resultado.get("error_contexto"), resultado

    print("✅ node_mcp detectó args incompletos sin reventar.")
    print(f"   error_mensaje = {resultado.get('error_mensaje')!r}")


def test_indice_correcto_en_plan_multitool():
    print("\n" + "=" * 70)
    print("TEST 3: node_mcp lee SUS PROPIOS args en un plan de 2 pasos")
    print("=" * 70)

    mock_manager = MagicMock()
    mock_manager.call_tool.return_value = "ok"
    original_get_manager = mcp_client_module.get_mcp_manager
    mcp_client_module.get_mcp_manager = lambda: mock_manager

    try:
        state = crear_estado_inicial(orden="tarea compuesta", mem={}, modo_autonomo=True)
        state["plan_pasos"] = [
            {"tool": "web", "instruccion": "buscar algo", "args": {"query": "otra cosa"}},
            {"tool": "mcp", "instruccion": "consultar server",
             "args": {"server": "notion", "name": "search", "arguments": {"query": "esto"}}},
        ]
        # Simulamos que node_mcp es el paso 2 (índice 1), como lo vería
        # dentro del sub_estado real de node_plan_executor.
        state["plan_index"] = 1

        resultado = gn.node_mcp(state)

        mock_manager.call_tool.assert_called_once_with("notion", "search", {"query": "esto"})
        assert not resultado.get("error_activo"), resultado

        print("✅ node_mcp leyó los args del paso 2 (los suyos), no los del paso 1 (web).")
    finally:
        mcp_client_module.get_mcp_manager = original_get_manager


if __name__ == "__main__":
    test_caso_feliz()
    test_caso_args_incompletos()
    test_indice_correcto_en_plan_multitool()
    print("\n" + "=" * 70)
    print("TODOS LOS TESTS DE node_mcp PASARON ✅")
    print("=" * 70)
