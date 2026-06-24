#!/usr/bin/env python3
"""
Tests Task 3: TOOL_REGISTRY + validar_plan().

Ejecutar: python tests/test_tool_registry.py
No requiere Ollama (validar_plan no importa graph_nodes).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent.tool_registry import (
    TOOL_REGISTRY,
    TOOLS_VALIDAS,
    validar_plan,
    get_node_func,
)


def test_registry_contiene_tools_base():
    for t in ("text", "web", "shell", "launch", "vision", "codigo", "memory"):
        assert t in TOOL_REGISTRY, f"falta tool {t}"
        assert "node" in TOOL_REGISTRY[t]


def test_plan_valido_simple():
    ok, errores = validar_plan([{"tool": "text", "instruccion": "saluda"}])
    assert ok, errores


def test_plan_valido_multitool():
    plan = [
        {"tool": "web", "instruccion": "busca precio bitcoin", "args": {"query": "precio bitcoin"}},
        {"tool": "shell", "instruccion": "guardar en archivo", "args": {"command": "echo x > p.txt"}},
    ]
    ok, errores = validar_plan(plan)
    assert ok, errores


def test_plan_tool_inexistente():
    ok, errores = validar_plan([{"tool": "inexistente", "instruccion": "x"}])
    assert not ok
    assert any("no existe" in e for e in errores)


def test_plan_paso_sin_tool():
    ok, errores = validar_plan([{"instruccion": "x"}])
    assert not ok
    assert any("falta el campo 'tool'" in e for e in errores)


def test_plan_no_lista():
    ok, errores = validar_plan({"tool": "text"})
    assert not ok


def test_plan_vacio():
    ok, errores = validar_plan([])
    assert not ok


def test_plan_instruccion_faltante():
    ok, errores = validar_plan([{"tool": "web"}])
    assert not ok
    assert any("instrucción" in e for e in errores)


def test_plan_args_no_dict():
    ok, errores = validar_plan([{"tool": "text", "instruccion": "x", "args": "no-dict"}])
    assert not ok
    assert any("'args' debe ser un dict" in e for e in errores)


def test_vision_no_requiere_instruccion():
    ok, errores = validar_plan([{"tool": "vision"}])
    assert ok, errores


def test_instruccion_en_args_query():
    ok, errores = validar_plan([{"tool": "web", "args": {"query": "algo"}}])
    assert ok, errores


def test_get_node_func_resuelve():
    # Import lazy real de graph_nodes; debe retornar callable
    func = get_node_func("text")
    assert callable(func)
    assert func.__name__ == "node_text"


def test_get_node_func_tool_invalida():
    try:
        get_node_func("inexistente")
        assert False, "debió lanzar KeyError"
    except KeyError:
        pass


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
