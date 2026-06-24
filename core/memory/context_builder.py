"""
Constructor de contexto para el LLM basado en memoria.

FIX APLICADO:
- Usa mem["conversacion"] de RAM en vez de obtener_ultimos_turnos() (eliminaba query redundante)
- Mantiene 100 turnos en contexto (antes solo 20)
- Agrega recuerdos de tabla 'recuerdos' con importancia >= 3
"""
from core.config.settings import MAX_TURNOS_CONTEXTO, CONTEXTO_CONV_MAX_CHARS
from core.memory.memory_manager import obtener_recuerdos


def construir_contexto_memoria(mem: dict) -> str:
    """
    Construye un string de contexto para inyectar en el prompt del agente.
    Incluye perfil, flatpaks conocidos, historial de comandos y conversación anterior.
    """
    partes = []
    prefs  = mem.get("preferencias") or {}
    nombre = prefs.get("nombre_usuario", "Cara")
    nav    = prefs.get("navegador", "brave")
    notas  = prefs.get("notas") or []

    partes.append(f"[PERFIL DEL CREADOR]\nNombre preferido: {nombre}. Navegador: {nav}.")

    if notas:
        partes.append("Notas personales: " + "; ".join(notas[:5]))

    # Recuerdos importantes de la tabla 'recuerdos' (excluyendo preferencias, ya mostradas arriba)
    recuerdos = obtener_recuerdos(importancia_min=3, limit=15)
    recuerdos = [r for r in recuerdos if r["categoria"] != "preferencias"]
    if recuerdos:
        lineas = [f"  • [{r['categoria']}] {r['contenido']}" for r in recuerdos]
        partes.append("\n[RECUERDOS GUARDADOS]\n" + "\n".join(lineas))

    flatpaks = mem.get("flatpaks") or {}
    if flatpaks:
        lista = ", ".join(f"{n} ({i})" for n, i in list(flatpaks.items())[:15])
        partes.append(f"\n[FLATPAKS CONOCIDOS — ya instalados]\n{lista}")

    historial = mem.get("historial_comandos") or []
    if historial:
        ultimos = historial[-5:]
        lineas  = [f"  • {e['fecha']} | {e['cmd']}" for e in ultimos]
        partes.append("\n[ÚLTIMOS COMANDOS EJECUTADOS]\n" + "\n".join(lineas))

    # Contexto conversacional: se inyectan hasta MAX_TURNOS_CONTEXTO turnos,
    # priorizando los MÁS RECIENTES y acotando por presupuesto de caracteres
    # (CONTEXTO_CONV_MAX_CHARS) para no desbordar la ventana del modelo.
    # Así se aumenta la memoria conversacional de forma SEGURA: si los turnos
    # recientes son largos, se incluyen menos; si son cortos, se incluyen más.
    turnos = (mem.get("conversacion") or [])[-MAX_TURNOS_CONTEXTO:]
    if turnos:
        seleccion: list[str] = []
        total = 0
        for t in reversed(turnos):  # del más reciente hacia atrás
            rol   = str(t.get("rol", "?")).upper()
            texto = str(t.get("texto", ""))
            linea = f"  [{rol}]: {texto}"
            if seleccion and total + len(linea) > CONTEXTO_CONV_MAX_CHARS:
                break  # presupuesto agotado → descartar turnos más viejos
            seleccion.append(linea)
            total += len(linea)
        seleccion.reverse()  # restaurar orden cronológico
        partes.append(
            f"\n[CONTEXTO DE SESIONES ANTERIORES] ({len(seleccion)} turnos)\n"
            + "\n".join(seleccion)
        )

    return "\n".join(partes)
