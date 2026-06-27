"""
context_builder.py — Constructor de contexto para Aether.

Construye el contexto que recibe el LLM antes de cada inferencia.
En vez de volcar todo el historial, arma slots independientes
y filtra por relevancia al tema actual.

Slots:
    [SISTEMA]         — core_memory (siempre presente)
    [RECUERDOS]       — hechos permanentes relevantes al tema
    [CONVERSACIÓN]    — solo turnos del tema actual (no todo el historial)
    [COMANDOS]        — últimos comandos ejecutados (solo si aplica)

Context Dump:
    Genera un log legible de qué entró y qué se descartó.
    Útil para depurar por qué el modelo respondió de cierta manera.
"""

from core.memory.memory_manager import (
    obtener_recuerdos,
    obtener_turnos_por_tema,
    obtener_ultimos_turnos,
)

# Temas que NO necesitan historial de comandos en el contexto
_TEMAS_SIN_COMANDOS = {"chat", "text", "memory", "web", ""}

# Máximo de turnos por tema a inyectar
MAX_TURNOS_TEMA   = 8
# Máximo de caracteres del slot de conversación
MAX_CHARS_CONV    = 3000
# Máximo de recuerdos a inyectar
MAX_RECUERDOS     = 10


# ══════════════════════════════════════════════════════════════════════
# BUILDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════

def construir_contexto_memoria(mem: dict, tema: str = "", sesion_id: str = "") -> str:
    """
    Construye el contexto para el LLM filtrando por relevancia.

    Args:
        mem:  dict RAM de Aether (cargado por memory_manager)
        tema: intención detectada por el planner ("web", "shell", "chat", etc.)
              Si está vacío, solo inyecta core_memory y recuerdos importantes.

    Returns:
        String listo para inyectar en el system prompt.
    """
    slots  = {}
    descartado = []

    # ── Slot 1: SISTEMA (core_memory — siempre presente) ─────────────
    core = mem.get("core") or {}
    if core:
        lineas = [f"  {k}: {v}" for k, v in core.items() if v]
        slots["SISTEMA"] = "\n".join(lineas)
    else:
        slots["SISTEMA"] = "  (sin datos de core)"

    # ── Slot 2: RECUERDOS relevantes al tema ─────────────────────────
    # Si hay tema, buscar recuerdos de esa categoría primero
    recuerdos = []
    if tema and tema not in ("text", "chat", ""):
        recuerdos = obtener_recuerdos(categoria=tema, importancia_min=1, limit=MAX_RECUERDOS)

    # Completar con recuerdos de alta importancia si hay espacio
    if len(recuerdos) < MAX_RECUERDOS:
        generales = obtener_recuerdos(importancia_min=2, limit=MAX_RECUERDOS - len(recuerdos))
        # Evitar duplicados
        ids_ya = {r.get("contenido") for r in recuerdos}
        recuerdos += [r for r in generales if r.get("contenido") not in ids_ya]

    if recuerdos:
        lineas = [
            f"  [{r.get('categoria', 'general')}] {r.get('contenido', '')}"
            for r in recuerdos
        ]
        slots["RECUERDOS"] = "\n".join(lineas)
    else:
        descartado.append("recuerdos (ninguno relevante)")

    # ── Slot 3: CONVERSACIÓN filtrada por tema ────────────────────────
        turnos_relevantes = []

    if tema:
        turnos_relevantes = obtener_turnos_por_tema(tema, limit=MAX_TURNOS_TEMA)

    if not turnos_relevantes:
        # Usar sesion_id explícito, o fallback al último de la RAM
        sesion_actual = sesion_id or _sesion_actual(mem)
        turnos_ram = mem.get("conversacion") or []
        if sesion_actual:
            turnos_relevantes = [
                t for t in turnos_ram if t.get("sesion_id") == sesion_actual
            ][-MAX_TURNOS_TEMA:]
        else:
            turnos_relevantes = turnos_ram[-3:]
            if len(turnos_ram) > 3:
                descartado.append(f"conversación ({len(turnos_ram) - 3} turnos antiguos omitidos)")

    if turnos_relevantes:
        seleccion = []
        total_chars = 0
        for t in reversed(turnos_relevantes):
            rol   = str(t.get("rol", "?")).upper()
            texto = str(t.get("texto", ""))
            linea = f"  [{rol}]: {texto}"
            if total_chars + len(linea) > MAX_CHARS_CONV:
                descartado.append(f"conversación (truncada por presupuesto de caracteres)")
                break
            seleccion.append(linea)
            total_chars += len(linea)
        seleccion.reverse()
        slots["CONVERSACIÓN"] = "\n".join(seleccion)
    else:
        descartado.append("conversación (sin historial relevante)")

    # ── Slot 4: COMANDOS (solo para temas que lo necesitan) ──────────
    if tema not in _TEMAS_SIN_COMANDOS:
        historial = mem.get("historial_comandos") or []
        if historial:
            ultimos = historial[-5:]
            lineas  = [f"  {e.get('fecha', '')} → {e.get('cmd', '')}" for e in ultimos]
            slots["COMANDOS RECIENTES"] = "\n".join(lineas)
        else:
            descartado.append("comandos (sin historial)")
    else:
        descartado.append(f"comandos (innecesarios para tema '{tema}')")

    # ── Ensamblar contexto final ──────────────────────────────────────
    partes = []
    for nombre, contenido in slots.items():
        partes.append(f"[{nombre}]\n{contenido}")

    return "\n\n".join(partes)


def construir_context_dump(mem: dict, tema: str = "") -> str:
    """
    Genera un log legible de qué información entra al modelo y qué se descarta.
    Llamar antes de cada inferencia para facilitar el debug.

    Ejemplo de salida:
        === CONTEXT DUMP ===
        Tema:      shell
        Slots:     SISTEMA, RECUERDOS, COMANDOS RECIENTES
        Descartado: conversación (sin historial relevante)
        Turnos inyectados: 3
        ====================
    """
    core      = mem.get("core") or {}
    turnos    = mem.get("conversacion") or []
    comandos  = mem.get("historial_comandos") or []

    recuerdos_count = len(
        obtener_recuerdos(categoria=tema if tema else None, importancia_min=1, limit=MAX_RECUERDOS)
    )
    turnos_tema = obtener_turnos_por_tema(tema, limit=MAX_TURNOS_TEMA) if tema else []

    slots_activos = ["SISTEMA"]
    if recuerdos_count:
        slots_activos.append("RECUERDOS")
    if turnos_tema or turnos:
        slots_activos.append("CONVERSACIÓN")
    if tema not in _TEMAS_SIN_COMANDOS and comandos:
        slots_activos.append("COMANDOS RECIENTES")

    lineas = [
        "=== CONTEXT DUMP ===",
        f"Tema:              {tema or '(sin tema)'}",
        f"Core memory:       {len(core)} claves",
        f"Slots activos:     {', '.join(slots_activos)}",
        f"Recuerdos:         {recuerdos_count} inyectados",
        f"Turnos (tema):     {len(turnos_tema)} inyectados de {len(turnos)} en RAM",
        f"Comandos en RAM:   {len(comandos)}",
        "====================",
    ]
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def _sesion_actual(mem: dict) -> str:
    """Retorna el sesion_id del turno más reciente en RAM."""
    turnos = mem.get("conversacion") or []
    if turnos:
        return turnos[-1].get("sesion_id", "")
    return ""