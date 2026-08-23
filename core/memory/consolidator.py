"""
consolidator.py — Consolidación de memoria de Aether.

Filosofía: la memoria de largo plazo NO es un log de mensajes crudos que se
inyecta cada vez más grande en el prompt (eso es lo que contamina el
contexto). En cambio, mantenemos UN resumen acumulativo (rolling summary)
que se REESCRIBE por completo cada vez que hay turnos nuevos que integrar
— no se le hace append.

Flujo:
    1. Se leen los turnos de `conversaciones` posteriores al último
       consolidado (resumen_memoria.ultimo_turno_id).
    2. Si hay suficientes turnos nuevos (o se fuerza), se le pide al modelo
       que reescriba el resumen completo integrando lo relevante y
       descartando ruido/charla efímera.
    3. El resultado reemplaza el resumen anterior en `resumen_memoria`.

El historial crudo en `conversaciones` NO se borra — sigue existiendo para
continuidad de sesión, resolución anafórica ("ejecutalo", "guardalo") y
`/historial` en la TUI. Lo que cambia es que el CONTEXTO inyectado al LLM
para memoria de largo plazo pasa a depender del resumen curado, no de un
dump creciente de mensajes.
"""

from __future__ import annotations

import threading

from core.memory.memory_manager import (
    obtener_resumen,
    guardar_resumen,
    obtener_turnos_pendientes_de_resumen,
)

# Umbral por defecto: no vale la pena gastar una llamada al LLM por cada
# turno nuevo. Se consolida cuando hay un lote razonable acumulado.
MIN_TURNOS_PARA_CONSOLIDAR = 12

# El consolidado puede tardar bastante más que un turno normal porque llama al
# LLM. Un único worker por proceso evita que dos respuestas simultáneas lean el
# mismo cursor y reescriban el resumen en competencia. Las solicitudes que
# lleguen mientras está activo se agrupan para una pasada adicional al final.
_scheduler_lock = threading.Lock()
_scheduler_running = False
_scheduler_requested_again = False
_memories_to_refresh: list[dict] = []

_SYSTEM_PROMPT = (
    "Sos el consolidador de memoria de Aether. Tu única salida es un resumen "
    "acumulativo actualizado — NO un log de conversación, NO una lista de "
    "turnos, NO explicaciones sobre lo que hiciste.\n\n"
    "Reglas:\n"
    "- Reescribís el resumen COMPLETO cada vez, integrando lo nuevo relevante "
    "y descartando charla efímera, saludos, o información que ya quedó "
    "obsoleta (ej: un problema que ya se resolvió, un plan que cambió).\n"
    "- Priorizá: hechos estables del usuario, proyectos activos y su estado, "
    "preferencias técnicas, decisiones tomadas, problemas pendientes.\n"
    "- Nunca inventes datos que no estén en el resumen previo o los turnos "
    "nuevos.\n"
    "- Prosa organizada en secciones cortas si hace falta, sin relleno, en "
    "español.\n"
    "- Si un turno nuevo contradice al resumen previo (ej: un dato cambió), "
    "el turno nuevo gana — no dejes versiones viejas y nuevas conviviendo."
)


def _bloque_turnos(turnos: list[dict]) -> str:
    lineas = []
    for t in turnos:
        rol = str(t.get("rol", "?")).upper()
        texto = str(t.get("texto", "")).strip()
        if texto:
            lineas.append(f"[{rol}] {texto}")
    return "\n".join(lineas)


def consolidar_resumen(forzar: bool = False, min_turnos: int = MIN_TURNOS_PARA_CONSOLIDAR) -> str | None:
    """
    Actualiza el resumen acumulativo con los turnos nuevos desde la última
    consolidación.

    Retorna el nuevo resumen si se consolidó, o None si no había turnos
    nuevos suficientes (y forzar=False) o si algo falló.

    Diseñada para llamarse en background / best-effort (ej. desde
    engine_bridge tras cada turno, o desde un comando manual /memory
    consolidar). Nunca debe romper el flujo principal si falla.
    """
    pendientes = obtener_turnos_pendientes_de_resumen()

    if not pendientes:
        return None
    if len(pendientes) < min_turnos and not forzar:
        return None

    actual = obtener_resumen()
    bloque = _bloque_turnos(pendientes)
    if not bloque.strip():
        return None

    user_prompt = f"""RESUMEN ACTUAL:
{actual.get("texto") or "(vacío — primera consolidación, no hay resumen previo todavía)"}

TURNOS NUEVOS A INTEGRAR:
{bloque}

Reescribí el resumen completo actualizado (solo el resumen, sin explicaciones ni comentarios fuera de él):"""

    try:
        # Import diferido: evita import circular (graph_nodes importa
        # memory_manager a nivel de módulo; consolidator solo lo necesita
        # en runtime, no al cargar el módulo).
        from core.agent.graph_nodes import _llm_chat, _parse_ornith_thinking

        raw = _llm_chat(system=_SYSTEM_PROMPT, user=user_prompt, min_predict=2048)
        _, nuevo_resumen = _parse_ornith_thinking(raw)
        nuevo_resumen = nuevo_resumen.strip()
    except Exception as e:
        print(f"[MEMORIA] Error consolidando resumen: {e}")
        return None

    if not nuevo_resumen:
        print("[MEMORIA] Consolidación devolvió resumen vacío; se descarta, no se pisa el anterior.")
        return None

    ultimo_id = pendientes[-1]["id"]
    guardar_resumen(nuevo_resumen, ultimo_turno_id=ultimo_id)
    print(f"[MEMORIA] Resumen consolidado: {len(pendientes)} turnos integrados.")
    return nuevo_resumen


def turnos_pendientes_count() -> int:
    """Cantidad de turnos crudos todavía no integrados al resumen. Para UI/debug."""
    return len(obtener_turnos_pendientes_de_resumen())


def programar_consolidacion(mem: dict | None = None) -> bool:
    """Programa una consolidación best-effort sin bloquear la respuesta.

    Devuelve ``True`` cuando crea el worker y ``False`` cuando ya había uno
    activo (la solicitud igual queda coalescida). Si el consolidado produce un
    resumen nuevo, todos los dicts de memoria recibidos durante el trabajo se
    actualizan para que el turno siguiente lo vea sin recargar SQLite.
    """
    global _scheduler_running, _scheduler_requested_again

    with _scheduler_lock:
        if isinstance(mem, dict) and not any(mem is item for item in _memories_to_refresh):
            _memories_to_refresh.append(mem)

        if _scheduler_running:
            _scheduler_requested_again = True
            return False

        _scheduler_running = True

    threading.Thread(
        target=_worker_consolidacion_programada,
        name="aether-memory-consolidator",
        daemon=True,
    ).start()
    return True


def _worker_consolidacion_programada() -> None:
    """Worker interno del scheduler; nunca propaga errores al caller."""
    global _scheduler_running, _scheduler_requested_again

    while True:
        try:
            nuevo_resumen = consolidar_resumen()
            if nuevo_resumen is not None:
                with _scheduler_lock:
                    memorias = list(_memories_to_refresh)
                for mem in memorias:
                    mem["resumen"] = nuevo_resumen
        except Exception as e:  # defensa extra: el scheduler no puede matar la TUI/API
            print(f"[MEMORIA] Worker de consolidación falló (no bloqueante): {e}")

        with _scheduler_lock:
            if _scheduler_requested_again:
                _scheduler_requested_again = False
                continue
            _scheduler_running = False
            _memories_to_refresh.clear()
            return
