"""
Builder del grafo LangGraph para Aether.

Grafo principal
───────────────
START → router → [web | shell | launch | vision | codigo | text | memory]
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
                           node_router
                                │
          ┌──────┬──────┬───────┼───────┬────────┬────────┐
          │      │      │       │       │        │        │
         web  shell  launch  vision  codigo   text   memory
          │      │      │               │
          └──────┴──────┴───────────────┘
                        │ (si error_activo=True)
                  node_error_diagnose ◄──────────────────┐
                        │                                │
                  node_error_confirm                     │
                   │          │                          │
              (cancelado)  (confirmado)                  │
                   │          │                          │
                  END   node_error_retry ────────────────┘
                              │ (si sigue fallando)
                        node_error_fallback
                              │
                        node_error_confirm ──► ...

          Todos los nodos exitosos → node_finalize → END
"""

from langgraph.graph import StateGraph, END

from graph_state import AetherState
from graph_nodes import (
    node_router,
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

def _route_intent(state: AetherState) -> str:
    """Enruta al nodo correcto según la intención detectada por el router."""
    return state.get("intent", "text")


def _route_after_execution(state: AetherState) -> str:
    """
    Después de ejecutar un nodo principal (shell/launch/codigo/web/text/vision/memory):
    - Si hay error → diagnose
    - Si done=True  → END
    - Si no         → finalize
    """
    if state.get("error_activo", False):
        return "error_diagnose"
    if state.get("done", False):
        return END
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
    - Si no             → finalize
    """
    if state.get("error_activo", False):
        return "error_diagnose"
    return "finalize"


def _route_after_diagnose(state: AetherState) -> str:
    """
    Después de diagnosticar:
    - Si fix_propuesto tiene contenido → confirm
    - Si no                            → fallback
    """
    if state.get("fix_propuesto", "").strip():
        return "error_confirm"
    return "error_fallback"


def _route_after_fallback(state: AetherState) -> str:
    """
    Después del fallback:
    - done=True         → END (sin alternativas)
    - error_activo=True → confirm (hay candidato alternativo)
    """
    if state.get("done", False):
        return END
    if state.get("error_activo", False):
        return "error_confirm"
    return "finalize"


# ══════════════════════════════════════════════════════════════════════
# BUILDER
# ══════════════════════════════════════════════════════════════════════

def build_graph():
    """
    Construye y compila el grafo de Aether.
    Retorna un CompiledGraph listo para invocar.
    """
    builder = StateGraph(AetherState)

    # ── Nodos principales ────────────────────────────────────────────
    builder.add_node("router",   node_router)
    builder.add_node("web",      node_web)
    builder.add_node("shell",    node_shell)
    builder.add_node("launch",   node_launch)
    builder.add_node("vision",   node_vision)
    builder.add_node("codigo",   node_codigo)
    builder.add_node("text",     node_text)
    builder.add_node("memory",   node_memory)
    builder.add_node("finalize", node_finalize)

    # ── Nodos del error handler ──────────────────────────────────────
    builder.add_node("error_diagnose", node_error_diagnose)
    builder.add_node("error_confirm",  node_error_confirm)
    builder.add_node("error_retry",    node_error_retry)
    builder.add_node("error_fallback", node_error_fallback)

    # ── Edges ────────────────────────────────────────────────────────

    # START → router
    builder.set_entry_point("router")

    # router → nodo según intent
    builder.add_conditional_edges(
        "router",
        _route_intent,
        {
            "web":    "web",
            "shell":  "shell",
            "launch": "launch",
            "vision": "vision",
            "codigo": "codigo",
            "text":   "text",
            "memory": "memory",
        },
    )

    # Cada nodo de ejecución → finalize o error_diagnose
    for nodo in ("web", "shell", "launch", "vision", "codigo", "text", "memory"):
        builder.add_conditional_edges(nodo, _route_after_execution, {
            "error_diagnose": "error_diagnose",
            "finalize":       "finalize",
            END:              END,
        })

    # error_diagnose → confirm o fallback
    builder.add_conditional_edges("error_diagnose", _route_after_diagnose, {
        "error_confirm":   "error_confirm",
        "error_fallback":  "error_fallback",
    })

    # error_confirm → END o error_retry
    builder.add_conditional_edges("error_confirm", _route_after_confirm, {
        END:            END,
        "error_retry":  "error_retry",
    })

    # error_retry → error_diagnose (loop) o finalize
    builder.add_conditional_edges("error_retry", _route_after_retry, {
        "error_diagnose": "error_diagnose",
        "finalize":       "finalize",
    })

    # error_fallback → END, error_confirm, o finalize
    builder.add_conditional_edges("error_fallback", _route_after_fallback, {
        END:             END,
        "error_confirm": "error_confirm",
        "finalize":      "finalize",
    })

    # finalize → END siempre
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
