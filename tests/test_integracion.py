#!/usr/bin/env python3
"""
Tests Task 8 (integración end-to-end): invoca el grafo REAL con los nodos
mockeados a nivel de LLM/tools, sin requerir Ollama.

Ejecutar: python tests/test_integracion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_builder import build_graph
from core.agent.graph_state import crear_estado_inicial


def _instalar_mocks():
    """Mockea LLM, tools y persistencia en graph_nodes. Retorna (restore)."""
    originales = {
        "_llm_chat":         gn._llm_chat,
        "registrar_turno":   gn.registrar_turno,
        "registrar_comando": gn.registrar_comando,
        "ejecutar_comando":  gn.ejecutar_comando,
        "ver_pantalla":      gn.ver_pantalla,
        "_planner_llm":      gn._planner_llm,
        "buscar_web":        gn.buscar_web,
        "leer_url":          gn.leer_url,
    }

    def fake_llm(system, user, on_token=None, stop=None, stop_regex=None):
        resp = "RESPUESTA_LLM"
        if on_token:
            on_token(resp)
        return resp

    class _FakeTool:
        def __init__(self, valor):
            self._valor = valor
        def invoke(self, *a, **k):
            return self._valor

    gn._llm_chat        = fake_llm
    gn.registrar_turno  = lambda *a, **k: None
    gn.registrar_comando = lambda *a, **k: None
    gn.ejecutar_comando = lambda cmd: ("salida simulada", False)
    gn.ver_pantalla     = lambda *a, **k: "pantalla simulada"
    gn.buscar_web       = _FakeTool("resultados web simulados (sin URL)")
    gn.leer_url         = _FakeTool("contenido url simulado")

    def restore():
        for k, v in originales.items():
            setattr(gn, k, v)

    return restore


def test_plan_un_paso_text_end_to_end():
    restore = _instalar_mocks()
    try:
        grafo = build_graph()
        estado = crear_estado_inicial("cuéntame algo interesante", {}, True)
        resultado = grafo.invoke(estado)
    finally:
        restore()

    assert resultado["done"] is True
    assert resultado["final_response"] == "RESPUESTA_LLM"
    # Fue un plan de 1 paso tool=text
    assert len(resultado["plan_pasos"]) == 1
    assert resultado["plan_pasos"][0]["tool"] == "text"


def test_plan_un_paso_launch_end_to_end():
    restore = _instalar_mocks()
    try:
        grafo = build_graph()
        estado = crear_estado_inicial("abre firefox", {}, True)
        resultado = grafo.invoke(estado)
    finally:
        restore()

    assert resultado["done"] is True
    assert len(resultado["plan_pasos"]) == 1
    assert resultado["plan_pasos"][0]["tool"] == "launch"


def test_plan_multitool_end_to_end():
    restore = _instalar_mocks()
    # Forzar un plan multi-tool válido vía el planner
    plan = [
        {"tool": "web", "instruccion": "buscar precio bitcoin", "args": {"query": "precio bitcoin"}},
        {"tool": "text", "instruccion": "resumir el precio encontrado"},
    ]
    gn._planner_llm = lambda orden, mem: plan
    try:
        grafo = build_graph()
        estado = crear_estado_inicial("busca el precio de bitcoin y guárdalo", {}, True)
        resultado = grafo.invoke(estado)
    finally:
        restore()

    assert resultado["done"] is True
    assert len(resultado["plan_pasos"]) == 2
    # Se ejecutaron ambos pasos
    assert len(resultado["plan_resultados"]) == 2
    # Hubo síntesis final (multi-paso) → final_response no vacío
    assert resultado["final_response"]


def test_memoria_vacia_no_crashea_end_to_end():
    # mem={} debe ser normalizada por la factory y NO provocar KeyError
    restore = _instalar_mocks()
    try:
        grafo = build_graph()
        estado = crear_estado_inicial("hola", {}, True)
        resultado = grafo.invoke(estado)
    finally:
        restore()

    assert resultado["done"] is True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fallos = 0
    for fn in fns:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except Exception as e:
            import traceback
            fallos += 1
            print(f"❌ {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - fallos}/{len(fns)} tests OK")
    sys.exit(1 if fallos else 0)
