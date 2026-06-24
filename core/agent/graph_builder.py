r"""
Builder del grafo LangGraph para Aether.

Grafo principal (CON TOOL PLANNING)
────────────────────────────────────
START → planner → [plan_executor (multi-tool) | router (single-tool)]

Si plan_activo=True:
  planner → plan_executor (loop por cada paso) → plan_synthesizer → finalize → END

Si plan_activo=False (fallback seguro):
  planner → router → [web | shell | launch | vision | codigo | text | memory]
          → finalize → END

Error handler (loop)
────────────────────
Cualquier nodo que detecte error_activo=True → error_diagnose
→ error_confirm → [END si usuario cancela | error_retry]
→ error_retry → [error_diagnose si sigue fallando | finalize si OK]

Si la web no ayuda → error_fallback → error_confirm → error_retry

Diagrama completo
─────────────────

         ┌──────────────────────────────────────────────┐
         │                    START                     │
         └──────────────────────┬───────────────────────┘
                                │
                         node_planner ◄────────────────┐
                      /                  \             │
           (plan_activo)              (fallback)       │
                /                          \           │
      plan_executor ──► loop ──► plan_synthesizer     │
                                      │                │
                                      │           node_router
                                      │                │
                                      │    ┌──────┬────┼────┬──────┐
                                      │    │      │    │    │      │
                                      │   web shell launch vision  ...
                                      │    │      │         │
                                      └────┴──────┴─────────┘
                                                  │ (si error_activo=True)
                                          node_error_diagnose ◄──────────┐
                                                  │                      │
                                          node_error_confirm             │
                                           │          │                  │
                                      (cancelado)  (confirmado)          │
                                           │          │                  │
                                          END   node_error_retry ────────┘
                                                      │ (si sigue fallando)
                                                node_error_fallback
                                                      │
                                                node_error_confirm ──► ...

          Todos los nodos exitosos → node_finalize → END

CAMBIOS vs versión anterior
----------------------------
+ Agregado node_planner como nuevo entry point
+ Agregado node_plan_executor para ejecutar pasos secuenciales
+ Agregado node_plan_synthesizer para sintetizar resultados del plan
+ Router ahora es un nodo secundario (solo se activa si planner falla o no se necesita)
+ Fallback seguro: si planner falla, el sistema usa el router original (sin cambios)
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


# ══════════════════════════════════════════════════════════════════════
# CONDICIONALES
# ══════════════════════════════════════════════════════════════════════

def _destino_post_plan(state: AetherState) -> str:
    """
    Decide a dónde ir cuando el plan no tiene error activo:
    - quedan pasos → plan_executor
    - terminó con 1 paso → finalize
    - terminó con varios → plan_synthesizer
    Incluye guardas de coherencia de los campos plan_*.
    """
    plan_pasos = state.get("plan_pasos")
    if not isinstance(plan_pasos, list) or len(plan_pasos) == 0:
        return "finalize"

    plan_index = state.get("plan_index", 0)
    if not isinstance(plan_index, int) or plan_index < 0:
        plan_index = 0

    if plan_index < len(plan_pasos):
        return "plan_executor"
    if len(plan_pasos) <= 1:
        return "finalize"
    return "plan_synthesizer"


def _route_plan_executor(state: AetherState) -> str:
    """
    Routing del loop del executor (Task 6 — con error handler integrado):
    - Si el paso marcó error_activo → error_diagnose (el handler intentará
      resolverlo y luego se reanuda el plan).
    - Si no, sigue la lógica normal de continuación del plan.
    """
    if state.get("error_activo", False):
        return "error_diagnose"
    return _destino_post_plan(state)


def _route_intent(state: AetherState) -> str:
    """Enruta al nodo correcto según la intención detectada por el router."""
    return state.get("intent", "text")


def _route_after_execution(state: AetherState) -> str:
    """
    Después de ejecutar un nodo principal (shell/launch/codigo/web/text/vision/memory):
    - Si hay error activo → diagnose
    - Si no              → finalize

    FIX 6: Eliminado el caso `done=True → END` que nunca ocurría aquí.
    Los nodos de ejecución solo marcan done=True cuando el usuario rechaza
    explícitamente ejecutar un comando (node_shell en modo manual). Ese caso
    también va a finalize para que la respuesta quede registrada en DB.
    """
    if state.get("error_activo", False):
        return "error_diagnose"
    return "finalize"


def _route_after_confirm(state: AetherState) -> str:
    """
    Después de que el usuario ve el diagnóstico y decide:
    - done=True (usuario canceló) → END
    - error_activo=True           → error_retry
    """
    if state.get("done", False):
        return END
    return "error_retry"


def _route_after_retry(state: AetherState) -> str:
    """
    Después de aplicar el fix:
    - error_activo=True → volver a diagnosticar
    - Si no             → reanudar el plan (siguiente paso / síntesis / finalize)
    """
    if state.get("error_activo", False):
        return "error_diagnose"
    return _destino_post_plan(state)


def _route_after_diagnose(state: AetherState) -> str:
    """
    Después de diagnosticar:
    - Si ya se alcanzó el límite de intentos → fallback
    - Si fix_propuesto tiene contenido → confirm
    - Si no                            → fallback
    
    FIX: Enforce límite de reintentos configurado en graph_state.py
    """
    intento_actual = state.get("error_intento", 0)
    max_intentos = state.get("error_max_intentos", 3)
    
    # Si ya agotamos los intentos, forzar fallback
    if intento_actual >= max_intentos:
        print(f"\n⚠️  [ERROR HANDLER]: Límite de {max_intentos} intentos alcanzado. Ejecutando fallback...")
        return "error_fallback"
    
    # Si hay fix propuesto, continuar con confirmación
    if state.get("fix_propuesto", "").strip():
        return "error_confirm"
    
    # Si no hay fix, ir a fallback
    return "error_fallback"


def _route_after_fallback(state: AetherState) -> str:
    """
    Después del fallback:
    - done=True         → END (sin alternativas, aborta limpio)
    - error_activo=True → confirm (hay candidato alternativo)
    - Si no             → reanudar el plan (siguiente paso / síntesis / finalize)
    """
    if state.get("done", False):
        return END
    if state.get("error_activo", False):
        return "error_confirm"
    return _destino_post_plan(state)


# ══════════════════════════════════════════════════════════════════════
# BUILDER
# ══════════════════════════════════════════════════════════════════════

def build_graph():
    """
    Construye y compila el grafo de Aether (motor unificado por tool planning).

    Flujo (Task 6 — con error handler integrado):
        START → planner → plan_executor (loop)
              → [finalize (1 paso) | plan_synthesizer → finalize] → END

        Si un paso marca error_activo:
        plan_executor → error_diagnose → error_confirm → error_retry
              → (éxito) reanuda el plan (plan_executor / synthesizer / finalize)
              → (sigue fallando) error_diagnose ... hasta límite → error_fallback

    - node_planner es la ÚNICA puerta de decisión: siempre produce un plan.
    - node_plan_executor reutiliza los nodos reales (node_web, node_shell, ...)
      llamándolos como funciones; por eso NO se registran como nodos del grafo.
    - El error handler existente se reutiliza y, al resolver, REANUDA el plan.

    Retorna un CompiledGraph listo para invocar.
    """
    builder = StateGraph(AetherState)

    # ── Nodos del motor de planning ──────────────────────────────────
    builder.add_node("planner",          node_planner)
    builder.add_node("plan_executor",    node_plan_executor)
    builder.add_node("plan_synthesizer", node_plan_synthesizer)
    builder.add_node("finalize",         node_finalize)

    # ── Nodos del error handler (reutilizados) ───────────────────────
    builder.add_node("error_diagnose", node_error_diagnose)
    builder.add_node("error_confirm",  node_error_confirm)
    builder.add_node("error_retry",    node_error_retry)
    builder.add_node("error_fallback", node_error_fallback)

    # ── Edges ────────────────────────────────────────────────────────

    # START → planner
    builder.set_entry_point("planner")

    # planner → plan_executor (el planner SIEMPRE activa un plan)
    builder.add_edge("planner", "plan_executor")

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

    # error_diagnose → confirm o fallback
    builder.add_conditional_edges("error_diagnose", _route_after_diagnose, {
        "error_confirm":  "error_confirm",
        "error_fallback": "error_fallback",
    })

    # error_confirm → END o error_retry
    builder.add_conditional_edges("error_confirm", _route_after_confirm, {
        END:           END,
        "error_retry": "error_retry",
    })

    # error_retry → diagnose (sigue fallando) o reanudar plan
    builder.add_conditional_edges("error_retry", _route_after_retry, {
        "error_diagnose":   "error_diagnose",
        "plan_executor":    "plan_executor",
        "plan_synthesizer": "plan_synthesizer",
        "finalize":         "finalize",
    })

    # error_fallback → END / confirm / reanudar plan
    builder.add_conditional_edges("error_fallback", _route_after_fallback, {
        END:                END,
        "error_confirm":    "error_confirm",
        "plan_executor":    "plan_executor",
        "plan_synthesizer": "plan_synthesizer",
        "finalize":         "finalize",
    })

    # plan_synthesizer → finalize
    builder.add_edge("plan_synthesizer", "finalize")

    # finalize → END
    builder.add_edge("finalize", END)

    return builder.compile()


# ── Instancia singleton del grafo ────────────────────────────────────
_graph = None


def get_graph():
    """Retorna el grafo compilado (singleton)."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset_graph():
    """Fuerza la recompilación del grafo (útil para testing o cambios en runtime)."""
    global _graph
    _graph = None