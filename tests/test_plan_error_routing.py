#!/usr/bin/env python3
"""
Tests de FALLO DE TOOL durante un plan (PROBLEMA 7 del diagnóstico).

Demuestra el camino:  planner → plan_executor → (tool falla) → error handler

Se verifica de forma determinista (sin Ollama y sin disparar el input() del
error handler) en dos niveles:
  1. node_plan_executor marca error_activo y acumula un resultado "[ERROR] ..."
     cuando la tool lanza una excepción o devuelve error_activo.
  2. _route_plan_executor (el router real del grafo) envía a "error_diagnose"
     cuando hay error_activo — para web, shell, launch y vision.

Ejecutar: python tests/test_plan_error_routing.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.tool_registry as tool_registry
from core.agent.graph_nodes import node_plan_executor
from core.agent.graph_builder import _route_plan_executor, _destino_post_plan
from core.agent.graph_state import crear_estado_inicial


def _estado_con_plan(plan, index=0, resultados=None):
    estado = crear_estado_inicial("orden original", {}, True)
    estado["plan_activo"] = True
    estado["plan_pasos"] = plan
    estado["plan_index"] = index
    estado["plan_resultados"] = resultados or []
    return estado


class _node_func_patch:
    def __init__(self, func):
        self.func = func
    def __enter__(self):
        self.orig = tool_registry.get_node_func
        tool_registry.get_node_func = lambda tool: self.func
        return self
    def __exit__(self, *a):
        tool_registry.get_node_func = self.orig


# ──────────────────────────────────────────────────────────────────────
# 1. Excepción de la tool durante el plan → error_activo + [ERROR] + routing
# ──────────────────────────────────────────────────────────────────────

def _verifica_fallo_por_excepcion(tool):
    def fake_node(sub_state):
        raise RuntimeError(f"fallo simulado en {tool}")

    with _node_func_patch(fake_node):
        estado = _estado_con_plan([{"tool": tool, "instruccion": "haz algo"}])
        out = node_plan_executor(estado)

    # El executor capturó la excepción sin romper el plan
    assert out["error_activo"] is True, f"{tool}: debe marcar error_activo"
    assert out["plan_resultados"][-1].startswith("[ERROR]"), out["plan_resultados"]
    # plan_index avanzó igualmente (no se queda atascado en el mismo paso)
    assert out["plan_index"] == 1, f"{tool}: plan_index debe avanzar"

    # El router del grafo envía al error handler
    estado_post = dict(estado)
    estado_post.update(out)
    destino = _route_plan_executor(estado_post)
    assert destino == "error_diagnose", f"{tool}: debe enrutar a error_diagnose, fue {destino}"


def test_fallo_web_va_a_error_handler():
    _verifica_fallo_por_excepcion("web")


def test_fallo_shell_va_a_error_handler():
    _verifica_fallo_por_excepcion("shell")


def test_fallo_launch_va_a_error_handler():
    _verifica_fallo_por_excepcion("launch")


def test_fallo_vision_va_a_error_handler():
    _verifica_fallo_por_excepcion("vision")


# ──────────────────────────────────────────────────────────────────────
# 2. La tool devuelve error_activo (sin lanzar) → también va al handler
# ──────────────────────────────────────────────────────────────────────

def test_tool_reporta_error_activo_va_a_error_handler():
    def fake_node(sub_state):
        return {"error_activo": True, "error_mensaje": "comando falló", "error_contexto": "shell"}

    with _node_func_patch(fake_node):
        estado = _estado_con_plan([{"tool": "shell", "instruccion": "x"}])
        out = node_plan_executor(estado)

    assert out["error_activo"] is True
    assert out["plan_resultados"][-1].startswith("[ERROR]")
    estado_post = dict(estado); estado_post.update(out)
    assert _route_plan_executor(estado_post) == "error_diagnose"


# ──────────────────────────────────────────────────────────────────────
# 3. Sin error → routing normal (sanity, no se desvía al handler)
# ──────────────────────────────────────────────────────────────────────

def test_sin_error_un_paso_va_a_finalize():
    def fake_node(sub_state):
        return {"final_response": "ok"}
    with _node_func_patch(fake_node):
        estado = _estado_con_plan([{"tool": "text", "instruccion": "x"}])
        out = node_plan_executor(estado)
    estado_post = dict(estado); estado_post.update(out)
    # 1 paso completado → finalize (no synthesizer, no error handler)
    assert _route_plan_executor(estado_post) == "finalize"


def test_sin_error_quedan_pasos_va_a_plan_executor():
    def fake_node(sub_state):
        return {"final_response": "paso 1 ok"}
    with _node_func_patch(fake_node):
        estado = _estado_con_plan(
            [{"tool": "web", "instruccion": "x"}, {"tool": "shell", "instruccion": "y"}]
        )
        out = node_plan_executor(estado)
    estado_post = dict(estado); estado_post.update(out)
    # Aún queda el paso 2 → vuelve a plan_executor (loop controlado)
    assert _route_plan_executor(estado_post) == "plan_executor"


def test_sin_error_multipaso_completo_va_a_synthesizer():
    estado = _estado_con_plan(
        [{"tool": "web", "instruccion": "x"}, {"tool": "shell", "instruccion": "y"}],
        index=2,
        resultados=["r1", "r2"],
    )
    # plan_index ya == len(pasos) y >1 paso → synthesizer
    assert _destino_post_plan(estado) == "plan_synthesizer"


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
