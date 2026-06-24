# 🧠 Tool Planning de Aether (arquitectura unificada)

Motor único basado en **LangGraph**. El `planner` es la **única puerta de
decisión**: siempre produce un plan (lista de pasos). Una tarea simple es un
plan de 1 paso; una tarea compleja es un plan multi-tool. El `executor`
ejecuta cada paso **reutilizando los nodos reales** del agente.

## Flujo del grafo

```
START → planner → plan_executor (loop)
      → [finalize (1 paso) | plan_synthesizer → finalize] → END

Si un paso marca error_activo:
  plan_executor → error_diagnose → error_confirm → error_retry
        → (éxito) reanuda el plan (plan_executor / synthesizer / finalize)
        → (sigue fallando, hasta el límite) error_fallback
```

- **planner** (`node_planner`): única decisión. Estrategia:
  1. Keywords deterministas → plan de 1 paso con esa tool.
  2. Si parece multi-tool (conector + ≥2 categorías) → pide plan al LLM y lo
     valida con `validar_plan()`.
  3. Fallback seguro: plan de 1 paso (`text` o la tool detectada).
- **plan_executor** (`node_plan_executor`): ejecuta un paso despachando al
  nodo real vía `TOOL_REGISTRY` (no reimplementa tools). Acumula resultados,
  inyecta el contexto de pasos previos y captura errores por paso.
- **plan_synthesizer** (`node_plan_synthesizer`): sólo en planes multi-paso;
  sintetiza todos los resultados en la respuesta final.
- **finalize** (`node_finalize`): registra la respuesta y marca `done`.
- **error handler**: se reutiliza tal cual y, al resolver, **reanuda el plan**.

## Contrato de plan

```json
[
  {"tool": "web",   "instruccion": "buscar el precio de bitcoin", "args": {"query": "precio bitcoin"}},
  {"tool": "shell", "instruccion": "guardar el resultado en un archivo", "args": {"command": "echo ... > p.txt"}}
]
```

- `tool` (obligatorio): debe existir en `TOOL_REGISTRY`
  (`text, web, shell, launch, vision, codigo, memory`).
- `instruccion` (obligatorio salvo `vision`): qué debe hacer el paso.
- `args` (opcional): dict con datos específicos (`query`, `command`, `app`, ...).

`validar_plan(plan)` (en `core/agent/tool_registry.py`) rechaza planes que no
cumplan el contrato antes de ejecutarlos.

## Contrato de memoria (`mem`)

`normalizar_mem(mem)` (en `core/memory/memory_manager.py`) garantiza el
esquema obligatorio y evita `KeyError`:

```python
{
  "preferencias": {"nombre_usuario": str, "navegador": str, "notas": list},
  "flatpaks": dict,
  "historial_comandos": list,
  "conversacion": list,
}
```

`crear_estado_inicial(orden, mem, modo_autonomo)` (en
`core/agent/graph_state.py`) normaliza la memoria y construye un `AetherState`
completo (incluidos los campos `plan_*`). Es la **única** forma de inicializar
el estado del grafo.

## Motor único (sin CrewAI)

- Entrada recomendada: `core/services/graph_service.py::procesar_orden_grafo`.
- El CLI (`run.py` → `cli/main.py` → `backend/core/aether_service.py`) usa este
  motor.
- `core/services/aether_service.py` (CrewAI) quedó **DEPRECADO** (además estaba
  roto al importar: dependía de símbolos inexistentes en `error_handler`).
- `node_router` se conserva como helper **deprecado**; ya no está cableado en
  el grafo (su lógica de keywords vive en `_detectar_intent_keywords`,
  reutilizada por el planner).

## Tests

Todos corren sin Ollama (mockean LLM/tools) y usan memoria normalizada:

```bash
python test_planning.py        # runner agregado de toda la suite tests/
# o individualmente:
python tests/test_memoria.py
python tests/test_estado.py
python tests/test_tool_registry.py
python tests/test_executor.py
python tests/test_planner.py
python tests/test_error_loop.py
python tests/test_servicio.py
python tests/test_integracion.py   # end-to-end con el grafo real (mocks)
```

## Garantías

- ✅ Determinista: keywords primero; el LLM sólo se consulta para multi-tool.
- ✅ Planes validados antes de ejecutar.
- ✅ Memoria con esquema obligatorio + acceso defensivo (`.get()`).
- ✅ Executor robusto: un paso que falla no rompe el plan (error handler +
  captura por paso).
- ✅ Fallback seguro: si el LLM no da un plan válido, plan de 1 paso.
