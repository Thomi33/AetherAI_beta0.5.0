# 🚀 Quick Start — Tool Planning (unificado)

## Idea

Motor único LangGraph. El `planner` siempre produce un **plan** (lista de
pasos). Tarea simple = plan de 1 paso; tarea compleja = plan multi-tool. El
`executor` ejecuta cada paso **reutilizando los nodos reales**.

## Flujo

```
START → planner → plan_executor (loop)
      → [finalize (1 paso) | plan_synthesizer → finalize] → END
   (errores de un paso → error handler → reanuda el plan)
```

## Piezas clave

| Componente | Archivo | Rol |
|---|---|---|
| `normalizar_mem` | `core/memory/memory_manager.py` | Esquema obligatorio de `mem` (evita KeyError) |
| `crear_estado_inicial` | `core/agent/graph_state.py` | Estado completo del grafo (incluye `plan_*`) |
| `TOOL_REGISTRY` / `validar_plan` | `core/agent/tool_registry.py` | Tools válidas + validación de planes |
| `node_planner` | `core/agent/graph_nodes.py` | Única puerta de decisión |
| `node_plan_executor` | `core/agent/graph_nodes.py` | Ejecuta pasos reutilizando nodos |
| `node_plan_synthesizer` | `core/agent/graph_nodes.py` | Síntesis final (multi-paso) |
| `procesar_orden_grafo` | `core/services/graph_service.py` | Entrada única del motor |

## Verificación rápida

```bash
python -c "from core.agent.graph_builder import build_graph; build_graph()"   # compila el grafo
python test_planning.py                                                       # toda la suite
```

## Notas

- `node_router` y `core/services/aether_service.py` (CrewAI) están **deprecados**.
- El planner usa keywords deterministas primero; el LLM sólo para multi-tool.
- Si el LLM no da un plan válido → fallback a plan de 1 paso.
