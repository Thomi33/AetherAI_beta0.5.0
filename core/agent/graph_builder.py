r"""
Builder del grafo LangGraph para Aether.

Grafo principal (CON TOOL PLANNING + CONTEXT MANAGER)
──────────────────────────────────────────────────────
START → planner → context_manager → plan_executor → ... → finalize → END

El context_manager se ejecuta SIEMPRE después del planner y antes de cualquier
inferencia. Decide qué información entra al modelo y genera el context_dump
para debug.
"""

from langgraph.graph import StateGraph, END

from core.agent.graph_state import AetherState
from core.agent.graph_nodes import (
    node_router,
    node_planner,
    node_plan_executor,
    node_plan_synthesizer,
    node_web,
    node_shell,
    node_launch,
    node_vision,
    node_codigo,
    node_text,
    node_memory,
    node_finalize,
    node_error_diagnose,
    node_error_confirm,
    node_error_retry,
    node_error_fallback,
)
from core.agent.node_context_manager import node_context_manager


# ══════════════════════════════════════════════════════════════════════
# CONDICIONALES
# ══════════════════════════════════════════════════════════════════════

_TOOLS_CON_RESUMEN = frozenset({"launch", "shell", "codigo", "file_write"})


def _destino_post_plan(state: AetherState) -> str:
    plan_pasos = state.get("plan_pasos")
    if not isinstance(plan_pasos, list) or len(plan_pasos) == 0:
        return "finalize"

    plan_index = state.get("plan_index", 0)
    if not isinstance(plan_index, int) or plan_index < 0:
        plan_index = 0

    if plan_index < len(plan_pasos):
        return "plan_executor"
    if len(plan_pasos) <= 1:
        paso = plan_pasos[0] if isinstance(plan_pasos[0], dict) else {}
        if paso.get("tool") in _TOOLS_CON_RESUMEN:
            return "plan_synthesizer"
        return "finalize"
    return "plan_synthesizer"


def _route_plan_executor(state: AetherState) -> str:
    if state.get("error_activo", False):
        return "error_diagnose"
    return _destino_post_plan(state)


def _route_after_execution(state: AetherState) -> str:
    if state.get("error_activo", False):
        return "error_diagnose"
    return "finalize"


def _route_after_confirm(state: AetherState) -> str:
    if state.get("done", False):
        return END
    return "error_retry"


def _route_after_retry(state: AetherState) -> str:
    if state.get("error_activo", False):
        return "error_diagnose"
    return _destino_post_plan(state)


def _route_after_diagnose(state: AetherState) -> str:
    intento_actual = state.get("error_intento", 0)
    max_intentos   = state.get("error_max_intentos", 3)
    if intento_actual >= max_intentos:
        print(f"\n⚠️  [ERROR HANDLER]: Límite de {max_intentos} intentos alcanzado. Ejecutando fallback...")
        return "error_fallback"
    if state.get("fix_propuesto", "").strip():
        return "error_confirm"
    return "error_fallback"


def _route_after_fallback(state: AetherState) -> str:
    if state.get("done", False):
        return END
    if state.get("error_activo", False):
        return "error_confirm"
    return _destino_post_plan(state)


# ══════════════════════════════════════════════════════════════════════
# BUILDER
# ══════════════════════════════════════════════════════════════════════

def build_graph():
    builder = StateGraph(AetherState)

    # ── Nodos ────────────────────────────────────────────────────────
    builder.add_node("planner",          node_planner)
    builder.add_node("context_manager",  node_context_manager)   # ← NUEVO
    builder.add_node("plan_executor",    node_plan_executor)
    builder.add_node("plan_synthesizer", node_plan_synthesizer)
    builder.add_node("finalize",         node_finalize)

    builder.add_node("error_diagnose", node_error_diagnose)
    builder.add_node("error_confirm",  node_error_confirm)
    builder.add_node("error_retry",    node_error_retry)
    builder.add_node("error_fallback", node_error_fallback)

    # ── Edges ────────────────────────────────────────────────────────

    # START → planner → context_manager → plan_executor
    builder.set_entry_point("planner")
    builder.add_edge("planner",         "context_manager")   # ← NUEVO
    builder.add_edge("context_manager", "plan_executor")     # ← NUEVO

    # plan_executor → loop / error / synthesizer / finalize
    builder.add_conditional_edges(
        "plan_executor",
        _route_plan_executor,
        {
            "plan_executor":    "plan_executor",
            "error_diagnose":   "error_diagnose",
            "plan_synthesizer": "plan_synthesizer",
            "finalize":         "finalize",
        },
    )

    builder.add_conditional_edges("error_diagnose", _route_after_diagnose, {
        "error_confirm":  "error_confirm",
        "error_fallback": "error_fallback",
    })

    builder.add_conditional_edges("error_confirm", _route_after_confirm, {
        END:           END,
        "error_retry": "error_retry",
    })

    builder.add_conditional_edges("error_retry", _route_after_retry, {
        "error_diagnose":   "error_diagnose",
        "plan_executor":    "plan_executor",
        "plan_synthesizer": "plan_synthesizer",
        "finalize":         "finalize",
    })

    builder.add_conditional_edges("error_fallback", _route_after_fallback, {
        END:                END,
        "error_confirm":    "error_confirm",
        "plan_executor":    "plan_executor",
        "plan_synthesizer": "plan_synthesizer",
        "finalize":         "finalize",
    })

    builder.add_edge("plan_synthesizer", "finalize")
    builder.add_edge("finalize",         END)

    return builder.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset_graph():
    global _graph
    _graph = None