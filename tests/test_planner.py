#!/usr/bin/env python3
"""
Tests Task 5: node_planner como única puerta de decisión.

Ejecutar: python tests/test_planner.py
No requiere Ollama: se mockea _planner_llm para el caso multi-tool.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import node_planner
from core.agent.graph_state import crear_estado_inicial
from core.agent.tool_registry import validar_plan


def _plan(orden):
    estado = crear_estado_inicial(orden, {}, True)
    return node_planner(estado)


def test_siempre_activa_plan():
    out = _plan("hola que tal")
    assert out["plan_activo"] is True
    assert isinstance(out["plan_pasos"], list) and len(out["plan_pasos"]) >= 1
    assert out["plan_index"] == 0
    assert out["plan_resultados"] == []


def test_launch_keyword_un_paso():
    out = _plan("abre firefox")
    assert len(out["plan_pasos"]) == 1
    assert out["plan_pasos"][0]["tool"] == "launch"


def test_web_keyword_un_paso():
    out = _plan("busca el precio del dólar")
    assert len(out["plan_pasos"]) == 1
    assert out["plan_pasos"][0]["tool"] == "web"


def test_conversacional_fallback_text():
    # Sin keyword reconocible → fallback a text (1 paso)
    out = _plan("cuéntame algo interesante por favor")
    assert len(out["plan_pasos"]) == 1
    assert out["plan_pasos"][0]["tool"] == "text"


def test_plan_generado_es_valido():
    for orden in ("abre firefox", "busca noticias", "cuéntame un chiste"):
        out = _plan(orden)
        ok, errores = validar_plan(out["plan_pasos"])
        assert ok, f"plan inválido para '{orden}': {errores}"


def test_multitool_usa_llm_y_valida():
    # Orden con conector + persistencia → intenta multi-tool vía LLM (mock)
    plan_falso = [
        {"tool": "web", "instruccion": "buscar precio bitcoin", "args": {"query": "precio bitcoin"}},
        {"tool": "shell", "instruccion": "guardar en archivo", "args": {"command": "echo x > p.txt"}},
    ]
    original = gn._planner_llm
    gn._planner_llm = lambda orden, mem: plan_falso
    try:
        out = _plan("busca el precio de bitcoin y guárdalo en un archivo")
    finally:
        gn._planner_llm = original

    assert len(out["plan_pasos"]) == 2
    assert out["plan_pasos"][0]["tool"] == "web"
    assert out["plan_pasos"][1]["tool"] == "shell"


def test_multitool_llm_invalido_cae_a_fallback():
    # Si el LLM devuelve plan inválido → fallback de 1 paso
    plan_invalido = [{"tool": "inexistente", "instruccion": "x"}]
    original = gn._planner_llm
    gn._planner_llm = lambda orden, mem: plan_invalido
    try:
        out = _plan("busca algo y guárdalo en archivo")
    finally:
        gn._planner_llm = original

    # Cae a fallback: 1 paso, tool válida (web por keyword 'busca')
    assert len(out["plan_pasos"]) == 1
    ok, _ = validar_plan(out["plan_pasos"])
    assert ok


def test_grafo_compila_con_nuevo_flujo():
    from core.agent.graph_builder import build_graph
    grafo = build_graph()
    assert grafo is not None


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
