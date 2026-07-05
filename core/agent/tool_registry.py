"""
Registro central de herramientas (tools) del grafo de Aether y validación de planes.

CONTRATO DE PLAN
────────────────
Un "plan" es una lista de "pasos". Cada paso es un dict con la forma:

    {
        "tool": "web",                 # obligatorio, debe existir en TOOL_REGISTRY
        "instruccion": "buscar ...",   # obligatorio, str no vacío (lo que debe hacer el paso)
        "args": {"query": "..."}        # opcional, dict con args específicos de la tool
    }

El executor (node_plan_executor) reutiliza los nodos reales del grafo: cada
tool mapea a una función de nodo (resuelta de forma lazy para evitar imports
circulares con graph_nodes).

DISEÑO
──────
- TOOL_REGISTRY es la única fuente de verdad de qué tools existen y qué
  función de nodo las ejecuta.
- validar_plan() NO importa graph_nodes: solo valida estructura y nombres,
  por lo que puede testearse de forma aislada y barata.
- get_node_func() hace el import lazy de graph_nodes cuando realmente se
  va a ejecutar un paso.
"""

from __future__ import annotations

from typing import Any, Callable


# ══════════════════════════════════════════════════════════════════════
# REGISTRO DE TOOLS
# ══════════════════════════════════════════════════════════════════════
# "node" = nombre de la función de nodo en core.agent.graph_nodes
# "instruccion_requerida" = si el paso debe traer una instrucción no vacía
# "descripcion" = ayuda para el planner (prompt) y debugging
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "text": {
        "node": "node_text",
        "instruccion_requerida": True,
        "descripcion": "Responder directamente o conversar usando el LLM.",
    },
    "web": {
        "node": "node_web",
        "instruccion_requerida": True,
        "descripcion": "Buscar información en internet y sintetizarla.",
    },
    "shell": {
        "node": "node_shell",
        "instruccion_requerida": True,
        "descripcion": "Generar y ejecutar un comando de sistema (shell).",
    },
    "launch": {
        "node": "node_launch",
        "instruccion_requerida": True,
        "descripcion": "Abrir/lanzar una aplicación (flatpak o binario en PATH).",
    },
    "vision": {
        "node": "node_vision",
        "instruccion_requerida": False,  # puede capturar pantalla sin instrucción explícita
        "descripcion": "Capturar la pantalla y analizar su contenido.",
    },
    "codigo": {
        "node": "node_codigo",
        "instruccion_requerida": True,
        "descripcion": "Generar código y ejecutarlo (python/bash).",
    },
    "memory": {
        "node": "node_memory",
        "instruccion_requerida": True,
        "descripcion": "Gestionar la memoria del agente (ver, borrar, recordar).",
    },
    "file_write": {
    "node": "node_file_write",
    "instruccion_requerida": False,  # puede inferir contenido de plan_resultados
    "descripcion": "Guardar el resultado de un paso anterior (o texto dado) en un archivo.",
     },
    "extract": {
        "node": "node_extract",
        "instruccion_requerida": False,  # opera sobre plan_resultados del paso previo
        "descripcion": (
            "Limpiar/extraer el contenido pedido por el usuario a partir del "
            "resultado crudo de un paso anterior (descarta metadatos de "
            "búsqueda, HTML sin decodificar y relleno conversacional)."
        ),
    },
    "mcp": {
        "node": "node_mcp",
        "instruccion_requerida": True,
        "descripcion": (
            "Invocar una tool de un servidor MCP externo conectado. "
            "El paso debe traer args={'server': <nombre>, 'name': <tool>, "
            "'arguments': {...}} identificando qué servidor y qué tool MCP usar."
        ),
    },
  }


# Conjunto de nombres de tools válidos (conveniencia)
TOOLS_VALIDAS: frozenset[str] = frozenset(TOOL_REGISTRY.keys())


# ══════════════════════════════════════════════════════════════════════
# VALIDACIÓN DE PLANES
# ══════════════════════════════════════════════════════════════════════

def _instruccion_de_paso(paso: dict) -> str:
    """
    Extrae la instrucción de un paso, aceptando varias ubicaciones por
    compatibilidad: top-level 'instruccion', o dentro de 'args'
    (instruccion/query/command/app).
    """
    if not isinstance(paso, dict):
        return ""
    if isinstance(paso.get("instruccion"), str) and paso["instruccion"].strip():
        return paso["instruccion"].strip()
    args = paso.get("args")
    if isinstance(args, dict):
        for clave in ("instruccion", "query", "command", "app", "orden"):
            val = args.get(clave)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def validar_plan(plan: Any) -> tuple[bool, list[str]]:
    """
    Valida la estructura de un plan antes de ejecutarlo.

    Reglas:
    - plan debe ser una lista no vacía
    - cada paso debe ser dict
    - cada paso debe tener 'tool' existente en TOOL_REGISTRY
    - si la tool requiere instrucción, el paso debe traer una no vacía
    - 'args' (si está) debe ser dict

    Retorna (ok, errores). ok=True solo si errores está vacío.
    """
    errores: list[str] = []

    if not isinstance(plan, list):
        return False, ["El plan debe ser una lista de pasos."]
    if len(plan) == 0:
        return False, ["El plan está vacío."]

    for i, paso in enumerate(plan):
        prefijo = f"paso {i + 1}"

        if not isinstance(paso, dict):
            errores.append(f"{prefijo}: no es un objeto/dict válido.")
            continue

        tool = paso.get("tool")
        if not tool:
            errores.append(f"{prefijo}: falta el campo 'tool'.")
            continue
        if tool not in TOOL_REGISTRY:
            errores.append(f"{prefijo}: tool '{tool}' no existe en el registro.")
            continue

        args = paso.get("args", {})
        if args is not None and not isinstance(args, dict):
            errores.append(f"{prefijo}: 'args' debe ser un dict.")

        if TOOL_REGISTRY[tool]["instruccion_requerida"]:
            if not _instruccion_de_paso(paso):
                errores.append(
                    f"{prefijo}: la tool '{tool}' requiere una instrucción no vacía."
                )

    return (len(errores) == 0), errores


# ══════════════════════════════════════════════════════════════════════
# RESOLUCIÓN LAZY DE NODOS
# ══════════════════════════════════════════════════════════════════════

def get_node_func(tool: str) -> Callable:
    """
    Resuelve la función de nodo asociada a una tool.

    Import lazy de core.agent.graph_nodes para evitar import circular
    (graph_nodes importa este módulo para el executor).
    """
    if tool not in TOOL_REGISTRY:
        raise KeyError(f"Tool desconocida: {tool}")

    import core.agent.graph_nodes as gn

    nombre_func = TOOL_REGISTRY[tool]["node"]
    func = getattr(gn, nombre_func, None)
    if func is None or not callable(func):
        raise AttributeError(
            f"La función de nodo '{nombre_func}' para la tool '{tool}' no existe."
        )
    return func
