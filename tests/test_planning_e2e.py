#!/usr/bin/env python3
"""
Tests END-TO-END del grafo compilado (planner → plan_executor → plan_synthesizer
→ finalize), 100% herméticos (sin Ollama / sin red / sin disco).

Cubre del diagnóstico:
- PROBLEMA 3: asserts EXPLÍCITOS sobre el nuevo sistema (plan_pasos/plan_resultados).
- PROBLEMA 5: multi-tool REAL (web→shell) con ejecución secuencial, transferencia
  de contexto entre pasos y resultados acumulados.
- PROBLEMA 6: ausencia de loops — N pasos producen EXACTAMENTE N despachos y
  plan_index avanza hasta el final; el grafo termina (sin GraphRecursionError).
- PROBLEMA 8: backwards-compat — orden simple ejecuta 1 sola tool y NO sintetiza.

Mocks: tool_registry.get_node_func (tools), graph_nodes._llm_chat (synthesizer),
graph_nodes._planner_llm (plan multi-tool), graph_nodes.registrar_turno (DB).

Ejecutar: python tests/test_planning_e2e.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
import core.agent.tool_registry as tool_registry
from core.agent.graph_builder import get_graph, reset_graph
from core.agent.graph_state import crear_estado_inicial


# ──────────────────────────────────────────────────────────────────────
# Infraestructura de mock
# ──────────────────────────────────────────────────────────────────────

class _Mocks:
    """Aplica/retira todos los parches necesarios para un grafo hermético."""
    def __init__(self, plan_multitool=None, salidas=None, intent_fallback="text"):
        self.plan_multitool = plan_multitool
        # salidas: dict tool -> dict de salida del nodo
        self.salidas = salidas or {}
        # tool que devuelve el clasificador LLM cuando no hay keyword
        self.intent_fallback = intent_fallback
        self.dispatches = []          # orden de tools despachadas
        self.ordenes_recibidas = {}   # tool -> orden (str) que recibió
        self.synth_calls = 0
        self._orig = {}

    def _fake_get_node_func(self, tool):
        def _fake_node(sub_state):
            self.dispatches.append(tool)
            self.ordenes_recibidas[tool] = sub_state.get("orden", "")
            return dict(self.salidas.get(tool, {"final_response": f"[{tool}] ok"}))
        return _fake_node

    def _fake_llm_chat(self, system, user, on_token=None):
        # Sólo el synthesizer usa _llm_chat en el setup mockeado
        self.synth_calls += 1
        return "RESPUESTA SINTETIZADA"

    def __enter__(self):
        self._orig["get_node_func"] = tool_registry.get_node_func
        self._orig["_llm_chat"] = gn._llm_chat
        self._orig["registrar_turno"] = gn.registrar_turno
        self._orig["_planner_llm"] = gn._planner_llm
        self._orig["_clasificar_intent_llm"] = gn._clasificar_intent_llm

        tool_registry.get_node_func = self._fake_get_node_func
        gn._llm_chat = self._fake_llm_chat
        gn.registrar_turno = lambda *a, **k: None
        gn._clasificar_intent_llm = lambda orden, mem: self.intent_fallback
        if self.plan_multitool is not None:
            gn._planner_llm = lambda orden, mem: self.plan_multitool
        reset_graph()  # recompilar limpio
        return self

    def __exit__(self, *a):
        tool_registry.get_node_func = self._orig["get_node_func"]
        gn._llm_chat = self._orig["_llm_chat"]
        gn.registrar_turno = self._orig["registrar_turno"]
        gn._planner_llm = self._orig["_planner_llm"]
        gn._clasificar_intent_llm = self._orig["_clasificar_intent_llm"]
        reset_graph()


_ORDEN_MULTI = "busca el precio de bitcoin y guarda el resultado en un archivo"


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 3 + 5 — multi-tool real web → shell
# ──────────────────────────────────────────────────────────────────────

def test_multitool_web_shell_resultados_acumulados():
    plan = [
        {"tool": "web", "instruccion": "buscar precio bitcoin", "args": {}},
        {"tool": "shell", "instruccion": "guardar en archivo", "args": {}},
    ]
    salidas = {
        "web":   {"web_results": "PRECIO_BTC=50000", "final_response": "PRECIO_BTC=50000"},
        "shell": {"shell_output": "archivo guardado", "final_response": "archivo guardado"},
    }
    with _Mocks(plan_multitool=plan, salidas=salidas) as m:
        estado = crear_estado_inicial(_ORDEN_MULTI, {}, True)
        result = get_graph().invoke(estado)

    # El planner ejecutó realmente un plan multi-tool
    assert len(result["plan_pasos"]) == 2, result["plan_pasos"]
    # Ejecución secuencial: 2 despachos, en orden web→shell
    assert m.dispatches == ["web", "shell"], m.dispatches
    # Resultados acumulados (PROBLEMA 5: assert len == 2)
    assert len(result["plan_resultados"]) == 2, result["plan_resultados"]


def test_multitool_transferencia_de_contexto():
    plan = [
        {"tool": "web", "instruccion": "buscar precio bitcoin", "args": {}},
        {"tool": "shell", "instruccion": "guardar en archivo", "args": {}},
    ]
    salidas = {
        "web":   {"final_response": "PRECIO_BTC=50000"},
        "shell": {"final_response": "ok"},
    }
    with _Mocks(plan_multitool=plan, salidas=salidas) as m:
        estado = crear_estado_inicial(_ORDEN_MULTI, {}, True)
        get_graph().invoke(estado)

    # El paso shell debe haber recibido el resultado del paso web en su orden
    orden_shell = m.ordenes_recibidas["shell"]
    assert "PRECIO_BTC=50000" in orden_shell, orden_shell
    assert "CONTEXTO DE PASOS PREVIOS" in orden_shell, orden_shell


def test_multitool_invoca_synthesizer():
    plan = [
        {"tool": "web", "instruccion": "x", "args": {}},
        {"tool": "shell", "instruccion": "y", "args": {}},
    ]
    with _Mocks(plan_multitool=plan) as m:
        estado = crear_estado_inicial(_ORDEN_MULTI, {}, True)
        result = get_graph().invoke(estado)
    # En plan multi-paso, el synthesizer corre (usa _llm_chat) y produce la final
    assert m.synth_calls == 1, m.synth_calls
    assert result["final_response"] == "RESPUESTA SINTETIZADA"


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 6 — ausencia de loops: N pasos → N despachos
# ──────────────────────────────────────────────────────────────────────

def test_no_loops_tres_pasos_tres_despachos():
    plan = [
        {"tool": "web",   "instruccion": "p1", "args": {}},
        {"tool": "shell", "instruccion": "p2", "args": {}},
        {"tool": "text",  "instruccion": "p3", "args": {}},
    ]
    with _Mocks(plan_multitool=plan) as m:
        estado = crear_estado_inicial(_ORDEN_MULTI, {}, True)
        result = get_graph().invoke(estado)
    # Exactamente 3 despachos (sin repetir pasos) → no hay loop infinito
    assert len(m.dispatches) == 3, m.dispatches
    # plan_index avanzó hasta el total
    assert result["plan_index"] == 3, result["plan_index"]
    assert len(result["plan_resultados"]) == 3


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 8 — backwards-compat: orden simple = 1 tool, sin síntesis
# ──────────────────────────────────────────────────────────────────────

def test_backwards_compat_single_tool_web():
    salidas = {"web": {"final_response": "Madrid es la capital"}}
    with _Mocks(salidas=salidas) as m:   # sin plan_multitool → keyword web
        estado = crear_estado_inicial("busca cuál es la capital de españa", {}, True)
        result = get_graph().invoke(estado)
    # Una sola tool ejecutada
    assert m.dispatches == ["web"], m.dispatches
    # Plan de 1 paso → NO se sintetiza
    assert m.synth_calls == 0
    assert result["final_response"] == "Madrid es la capital"
    assert len(result["plan_resultados"]) == 1


def test_backwards_compat_single_tool_text():
    salidas = {"text": {"final_response": "¡Hola! Estoy bien."}}
    with _Mocks(salidas=salidas) as m:
        estado = crear_estado_inicial("hola cómo estás", {}, True)
        result = get_graph().invoke(estado)
    assert m.dispatches == ["text"], m.dispatches
    assert m.synth_calls == 0
    assert result["final_response"] == "¡Hola! Estoy bien."


def test_backwards_compat_single_tool_launch():
    salidas = {"launch": {"final_response": "firefox lanzado"}}
    with _Mocks(salidas=salidas) as m:
        estado = crear_estado_inicial("abre firefox", {}, True)
        result = get_graph().invoke(estado)
    assert m.dispatches == ["launch"], m.dispatches
    assert m.synth_calls == 0


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
