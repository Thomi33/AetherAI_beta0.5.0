"""
Constructor de contexto para el LLM basado en memoria.
"""
from core.memory.memory_manager import obtener_ultimos_turnos


def construir_contexto_memoria(mem: dict) -> str:
    """
    Construye un string de contexto para inyectar en el prompt del agente.
    Incluye perfil, flatpaks conocidos, historial de comandos y conversación anterior.
    """
    partes = []
    nombre = mem["preferencias"].get("nombre_usuario", "Cara")
    nav    = mem["preferencias"].get("navegador", "brave")
    notas  = mem["preferencias"].get("notas", [])

    partes.append(f"[PERFIL DEL CREADOR]\nNombre preferido: {nombre}. Navegador: {nav}.")

    if notas:
        partes.append("Notas personales: " + "; ".join(notas[:5]))

    if mem["flatpaks"]:
        lista = ", ".join(
            f"{n} ({i})" for n, i in list(mem["flatpaks"].items())[:15]
        )
        partes.append(f"\n[FLATPAKS CONOCIDOS — ya instalados]\n{lista}")

    if mem["historial_comandos"]:
        ultimos = mem["historial_comandos"][-5:]
        lineas  = [f"  • {e['fecha']} | {e['cmd']}" for e in ultimos]
        partes.append("\n[ÚLTIMOS COMANDOS EJECUTADOS]\n" + "\n".join(lineas))

    ultimos = obtener_ultimos_turnos(20)
    if ultimos:
        lineas = [f"  [{fila['rol'].upper()}]: {fila['texto']}" for fila in ultimos]
        partes.append("\n[CONTEXTO DE SESIONES ANTERIORES]\n" + "\n".join(lineas))

    return "\n".join(partes)
