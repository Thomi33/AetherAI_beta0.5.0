from typing import Any
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage

IntentType = str

MAX_INTENTOS_DEFAULT = 3

MAX_INTENTOS_POR_CONTEXTO = {
    "launch": 5,
    "shell":  3,
    "codigo": 3,
}

class AetherState(TypedDict):
    # ── Entrada ──────────────────────────────────────────────────────
    orden:          str
    mem:            dict[str, Any]
    modo_autonomo:  bool

    # ── Control de flujo ─────────────────────────────────────────────
    intent:         str           # detectado por node_router
    done:           bool          # True → el grafo debe terminar
    terminado:      bool          # alias legacy (no usar en nodos nuevos)
    tokens:         list          # tokens de streaming acumulados
    ruta:           str           # ruta de debug

    # ── Tool Planning ────────────────────────────────────────────────
    plan_activo:    bool          # True si hay un plan multi-tool en ejecución
    plan_pasos:     list[dict]    # [{"tool": "web", "query": "..."}, {"tool": "shell", ...}]
    plan_index:     int           # índice del paso actual
    plan_resultados: list[str]    # resultados de cada paso ejecutado

    # ── Mensajes LangChain ───────────────────────────────────────────
    messages:       list[BaseMessage]

    # ── Respuestas ───────────────────────────────────────────────────
    respuesta:      str | None    # campo legacy
    llm_response:   str | None    # respuesta cruda del LLM
    final_response: str | None    # respuesta final al usuario

    # ── Shell / ejecución ────────────────────────────────────────────
    shell_command:  str | None    # comando que se ejecutó
    shell_output:   str           # stdout+stderr del comando
    shell_error:    bool          # True si hubo error de ejecución
    hubo_error:     bool          # alias legacy

    # ── Web ──────────────────────────────────────────────────────────
    web_results:    str           # resultados de buscar_web()

    # ── MCP ──────────────────────────────────────────────────────────
    mcp_result:     str           # resultado crudo devuelto por node_mcp

    # ── Visión ───────────────────────────────────────────────────────
    vision_result:  str           # descripción retornada por ver_pantalla()

    # ── Context Manager ──────────────────────────────────────────────
    sesion_id:        str           # identificador único de sesión (UUID)
    context_slots:    dict          # {"tema": "...", "contexto": "...", ...}
    context_dump:     str           # log legible de qué se inyectó
    tema_actual:      str           # tool/tema detectado (ej. "web", "shell", etc.)
    historial_filtrado: list        # turnos relevantes (para debug)

    # ── Código ───────────────────────────────────────────────────────
    _codigo_original: str         # código generado antes del primer error
    _archivo_codigo:  str         # ruta del archivo temporal (.py/.sh)

    # ── Error handler ────────────────────────────────────────────────
    error_activo:          bool
    error_contexto:        str    # "shell" | "launch" | "codigo"
    error_mensaje:         str
    error_cmd:             str    # campo legacy (usar shell_command)
    error_codigo_original: str    # campo legacy (usar _codigo_original)
    error_archivo:         str    # campo legacy (usar _archivo_codigo)
    error_intento:         int
    error_max_intentos:    int
    error_autorizado:      bool

    # Fix propuesto por node_error_diagnose
    fix_propuesto:   str          # comando o código corregido
    fix_diff:        str          # diff unificado (solo contexto "codigo")
    fix_fuente_web:  str          # URL fuente del fix

    # Aliases legacy mantenidos por compatibilidad con nodos anteriores
    error_fix_propuesto: str
    error_fix_diff:      str
    error_fix_fuente:    str


# ══════════════════════════════════════════════════════════════════════
# FACTORY DE ESTADO INICIAL
# ══════════════════════════════════════════════════════════════════════

def crear_estado_inicial(orden: str, mem: dict, modo_autonomo: bool = True) -> "AetherState":
    """
    Construye un AetherState completo y coherente para invocar el grafo.

    Garantiza que TODOS los campos existan con defaults seguros (incluidos
    los campos de tool planning), evitando KeyError en nodos. La memoria se
    normaliza con normalizar_mem() para cumplir el esquema obligatorio.

    Es la única fuente de verdad para inicializar el estado del grafo; tanto
    el CLI/servicio como los tests deben usar esta factory en vez de armar
    el dict a mano.
    """
    # Import local para evitar import circular (memory_manager → no depende de state)
    from core.memory.memory_manager import normalizar_mem

    mem_norm = normalizar_mem(mem)

    return {
        # ── Entrada ──────────────────────────────────────────────────
        "orden":         orden,
        "mem":           mem_norm,
        "modo_autonomo": modo_autonomo,

        # ── Control de flujo ─────────────────────────────────────────
        "intent":        "",
        "done":          False,
        "terminado":     False,
        "tokens":        [],
        "ruta":          "",

        # ── Tool Planning ────────────────────────────────────────────
        "plan_activo":     False,
        "plan_pasos":      [],
        "plan_index":      0,
        "plan_resultados": [],

        # ── Mensajes ─────────────────────────────────────────────────
        "messages":      [],

        # ── Respuestas ───────────────────────────────────────────────
        "respuesta":      None,
        "llm_response":   None,
        "final_response": None,

        # ── Shell ────────────────────────────────────────────────────
        "shell_command": None,
        "shell_output":  "",
        "shell_error":   False,
        "hubo_error":    False,

        # ── Web / MCP / Visión / Código ──────────────────────────────
        "web_results":      "",
        "mcp_result":       "",
        "vision_result":    "",
        "_codigo_original": "",
        "_archivo_codigo":  "",

        # ── Context Manager ──────────────────────────────────────────
        "sesion_id":        mem_norm.get("sesion_id", ""),
        "context_slots":    {},
        "context_dump":     "",
        "tema_actual":      "",
        "historial_filtrado": [],

        # ── Error handler ────────────────────────────────────────────
        "error_activo":          False,
        "error_contexto":        "",
        "error_mensaje":         "",
        "error_cmd":             "",
        "error_codigo_original": "",
        "error_archivo":         "",
        "error_intento":         0,
        "error_max_intentos":    MAX_INTENTOS_DEFAULT,
        "error_autorizado":      False,
        "fix_propuesto":         "",
        "fix_diff":              "",
        "fix_fuente_web":        "",
        "error_fix_propuesto":   "",
        "error_fix_diff":        "",
        "error_fix_fuente":      "",
    }


def _claves_estado() -> set[str]:
    """Retorna el conjunto de claves esperadas en AetherState (para tests/validación)."""
    return set(AetherState.__annotations__.keys())