#!/usr/bin/env python3
"""
Tests Task 4: node_plan_executor despacha a nodos reales (mockeados).

Ejecutar: python tests/test_executor.py
No requiere Ollama: se mockea get_node_func.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.tool_registry as tool_registry
from core.agent.graph_nodes import node_plan_executor
from core.agent.graph_state import crear_estado_inicial


def _estado_con_plan(plan, resultados=None, index=0):
    estado = crear_estado_inicial("orden original", {}, True)
    estado["plan_activo"] = True
    estado["plan_pasos"] = plan
    estado["plan_index"] = index
    estado["plan_resultados"] = resultados or []
    return estado


def test_despacha_al_nodo_correcto():
    llamadas = {}

    def fake_node(sub_state):
        llamadas["tool"] = "text"
        llamadas["orden"] = sub_state["orden"]
        llamadas["mem"] = sub_state["mem"]
        return {"final_response": "hola mundo", "messages": []}

    original = tool_registry.get_node_func
    tool_registry.get_node_func = lambda tool: fake_node
    try:
        estado = _estado_con_plan([{"tool": "text", "instruccion": "saluda"}])
        out = node_plan_executor(estado)
    finally:
        tool_registry.get_node_func = original

    assert "saluda" in llamadas["orden"], "la instrucción debe llegar como orden"
    assert isinstance(llamadas["mem"], dict) and "core" in llamadas["mem"]
    assert out["plan_resultados"][-1] == "hola mundo"


def test_acumula_resultados_y_contexto_previo():
    capturado = {}

    def fake_node(sub_state):
        capturado["orden"] = sub_state["orden"]
        return {"final_response": "segundo resultado"}

    original = tool_registry.get_node_func
    tool_registry.get_node_func = lambda tool: fake_node
    try:
        estado = _estado_con_plan(
            [{"tool": "web", "instruccion": "buscar X"}, {"tool": "shell", "instruccion": "guardar"}],
            resultados=["primer resultado"],
            index=1,
        )
        out = node_plan_executor(estado)
    finally:
        tool_registry.get_node_func = original

    # El contexto previo debe inyectarse en la orden del nodo
    assert "primer resultado" in capturado["orden"]
    assert out["plan_index"] == 2
    assert out["plan_resultados"] == ["primer resultado", "segundo resultado"]


def test_captura_excepcion_de_nodo():
    def fake_node(sub_state):
        raise RuntimeError("boom")

    original = tool_registry.get_node_func
    tool_registry.get_node_func = lambda tool: fake_node
    try:
        estado = _estado_con_plan([{"tool": "shell", "instruccion": "rompe"}])
        out = node_plan_executor(estado)
    finally:
        tool_registry.get_node_func = original

    assert out["error_activo"] is True
    assert "boom" in out["error_mensaje"]
    assert out["plan_index"] == 1
    assert out["plan_resultados"][-1].startswith("[ERROR]")


def test_indice_fuera_de_rango():
    estado = _estado_con_plan([{"tool": "text", "instruccion": "x"}], index=5)
    out = node_plan_executor(estado)
    assert out.get("plan_activo") is False


def test_propaga_error_activo_del_nodo():
    def fake_node(sub_state):
        return {"error_activo": True, "error_mensaje": "fallo shell", "error_contexto": "shell"}

    original = tool_registry.get_node_func
    tool_registry.get_node_func = lambda tool: fake_node
    try:
        estado = _estado_con_plan([{"tool": "shell", "instruccion": "x"}])
        out = node_plan_executor(estado)
    finally:
        tool_registry.get_node_func = original

    assert out["error_activo"] is True
    assert out["plan_resultados"][-1].startswith("[ERROR]")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fallos = 0
    for fn in fns:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except Exception as e:
            fallos += 1
            print(f"❌ {fn.__name__}: {e}")
    print(f"\n{len(fns) - fallos}/{len(fns)} tests OK")
    sys.exit(1 if fallos else 0)
