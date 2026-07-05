def test_mcp_planner_infiere_args():
    estado = crear_estado_inicial("listame directorios usando MCP", {})
    resultado = node_planner(estado)
    assert resultado["plan_pasos"][0]["tool"] == "mcp"
    assert resultado["plan_pasos"][0]["args"]["name"] == "list_directory"