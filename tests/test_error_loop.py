#!/usr/bin/env python3
"""
Tests Task 6: loop robusto + integración error handler + coherencia de estado.

Ejecutar: python tests/test_error_loop.py
No requiere Ollama: testea las funciones de routing y la compilación del grafo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent.graph_builder import (
    _route_plan_executor,
    _route_after_retry,
    _route_after_fallback,
    _destino_post_plan,
    build_graph,
)
from langgraph.graph import END


def _st(**kw):
    base = {"plan_pasos": [], "plan_index": 0, "error_activo": False, "done": False}
    base.update(kw)
    return base


# ── _route_plan_executor ──────────────────────────────────────────────

def test_executor_error_va_a_diagnose():
    s = _st(plan_pasos=[{"tool": "shell"}], plan_index=1, error_activo=True)
    assert _route_plan_executor(s) == "error_diagnose"


def test_executor_quedan_pasos_loop():
    s = _st(plan_pasos=[{"tool": "web"}, {"tool": "shell"}], plan_index=1)
    assert _route_plan_executor(s) == "plan_executor"


def test_executor_un_paso_va_a_finalize():
    s = _st(plan_pasos=[{"tool": "text"}], plan_index=1)
    assert _route_plan_executor(s) == "finalize"


def test_executor_multi_paso_va_a_synthesizer():
    s = _st(plan_pasos=[{"tool": "web"}, {"tool": "shell"}], plan_index=2)
    assert _route_plan_executor(s) == "plan_synthesizer"


# ── coherencia de estado ───────────────────────────────────────────────

def test_coherencia_plan_pasos_ausente():
    s = _st(plan_pasos=None, plan_index=0)
    assert _route_plan_executor(s) == "finalize"


def test_coherencia_indice_negativo():
    s = _st(plan_pasos=[{"tool": "web"}, {"tool": "shell"}], plan_index=-3)
    # índice negativo se trata como 0 → quedan pasos → loop
    assert _route_plan_executor(s) == "plan_executor"


def test_coherencia_plan_vacio():
    s = _st(plan_pasos=[], plan_index=0)
    assert _destino_post_plan(s) == "finalize"


# ── _route_after_retry (reanudación del plan) ──────────────────────────

def test_retry_sigue_fallando_diagnose():
    s = _st(plan_pasos=[{"tool": "shell"}], plan_index=1, error_activo=True)
    assert _route_after_retry(s) == "error_diagnose"


def test_retry_exito_reanuda_paso_siguiente():
    s = _st(plan_pasos=[{"tool": "web"}, {"tool": "shell"}], plan_index=1, error_activo=False)
    assert _route_after_retry(s) == "plan_executor"


def test_retry_exito_un_paso_accion_synthesizer():
    # Tras un retry exitoso, una tool de ACCIÓN de 1 paso (shell) pasa por el
    # synthesizer para que el modelo redacte el cierre (Fix 9), no a finalize.
    s = _st(plan_pasos=[{"tool": "shell"}], plan_index=1, error_activo=False)
    assert _route_after_retry(s) == "plan_synthesizer"


def test_retry_exito_multi_synthesizer():
    s = _st(plan_pasos=[{"tool": "web"}, {"tool": "shell"}], plan_index=2, error_activo=False)
    assert _route_after_retry(s) == "plan_synthesizer"


# ── _route_after_fallback ──────────────────────────────────────────────

def test_fallback_done_va_a_end():
    s = _st(done=True)
    assert _route_after_fallback(s) == END


def test_fallback_error_va_a_confirm():
    s = _st(error_activo=True)
    assert _route_after_fallback(s) == "error_confirm"


def test_fallback_exito_reanuda_plan():
    s = _st(plan_pasos=[{"tool": "web"}, {"tool": "shell"}], plan_index=1)
    assert _route_after_fallback(s) == "plan_executor"


# ── compilación del grafo con error handler ────────────────────────────

def test_grafo_compila_con_error_handler():
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
