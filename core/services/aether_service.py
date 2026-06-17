"""
Servicio Aether: interfaz principal que orquesta todo el sistema.
Mantiene compatibilidad con la API existente de jarvis.py
"""
import re

from core.memory.memory_manager import (
    registrar_turno,
    registrar_comando,
    guardar_memoria,
)
from core.tools.shell_executor import ejecutar_comando
from core.tools.flatpak_manager import (
    buscar_flatpak_en_memoria,
    intentar_lanzar_flatpak,
    actualizar_flatpaks,
)
from core.tools.file_writer import escribir_archivo
from core.tools.vision import ver_pantalla
from core.parser.shell_parser import extraer_comando_shell
from core.parser.response_parser import analizar_salida
from core.agent.builder import construir_agente
from core.agent.executor import crear_tarea, ejecutar_crew


# Palabras clave para detectar órdenes de escritura
PALABRAS_CLAVE_ESCRITURA = frozenset([
    "escribe", "crea", "guardar", "guarda", "crear",
    "archivo", "txt", "reporte", "documento", "informe", "json", "md",
])

PALABRAS_CLAVE_VISION = frozenset([
    "mira", "observa", "captura", "screenshot",
    "pantalla", "ves", "qué contenido hay",
    "que contenido hay", "qué ves", "que ves"
])

PALABRAS_CLAVE_LANZAR = frozenset([
    "ejecuta", "abre", "lanza", "inicia", "corre"
])


def _contiene_escritura(orden: str) -> bool:
    """Detecta si orden solicita escribir archivo."""
    return bool(set(orden.lower().split()) & PALABRAS_CLAVE_ESCRITURA)


def _contiene_vision(orden: str) -> bool:
    """Detecta si orden solicita captura de pantalla."""
    return any(x in orden.lower() for x in PALABRAS_CLAVE_VISION)


def _contiene_lanzar(orden: str) -> bool:
    """Detecta si orden es para lanzar/abrir aplicación."""
    return any(x in orden.lower() for x in PALABRAS_CLAVE_LANZAR)


def _procesar_orden(orden: str, mem: dict) -> str:
    """
    Función principal que orquesta el procesamiento de órdenes del usuario.
    Mantiene compatibilidad total con la API existente.
    
    Retorna respuesta del sistema.
    """
    # Construir agente y tarea
    agente = construir_agente(mem, con_tools=False)
    tarea = crear_tarea(orden, agente)
    
    # Ejecutar a través de CrewAI
    respuesta = ejecutar_crew(agente, tarea)
    
    # Extraer y ejecutar comando si lo hay
    comando = extraer_comando_shell(respuesta)
    
    if comando:
        salida, _ = ejecutar_comando(comando)
        return salida
    
    return respuesta


def procesar_orden_completo(orden: str, mem: dict, modo_autonomo: bool = True) -> str:
    """
    Procesamiento COMPLETO de orden incluyendo lógica de memoria y transacciones.
    Usado por el main loop y aether_service CLI.
    """
    # Registrar entrada del usuario
    registrar_turno(mem, "usuario", orden)
    
    # ATAJO 1: Órdenes de memoria
    if _procesar_comando_memoria(orden, mem):
        return None  # Ya impreso en la función
    
    # ATAJO 2: Visión de pantalla
    if _contiene_vision(orden):
        return _manejar_vision(orden)
    
    # ATAJO 3: Flatpak conocido
    if _contiene_lanzar(orden):
        app_id = buscar_flatpak_en_memoria(mem, orden)
        if app_id:
            print(f"\n🚀 [MEMORIA]: {app_id} conocido. Lanzando directamente...")
            salida, _ = ejecutar_comando(f"flatpak run {app_id}")
            print(salida)
            registrar_comando(mem, orden, f"flatpak run {app_id}")
            registrar_turno(mem, "jarvis", f"Lanzado {app_id} directamente desde memoria.")
            return f"Lanzado {app_id}"
    
    # FLUJO NORMAL: Procesar con agente
    agente = construir_agente(mem, con_tools=False)
    tarea = crear_tarea(orden, agente)
    respuesta = ejecutar_crew(agente, tarea)
    
    comando = extraer_comando_shell(respuesta)
    
    if comando:
        respuesta_limpia = re.sub(
            r"\[SHELL\].*?\[/SHELL\]|```[\w]*\n.*?\n```",
            "", respuesta, flags=re.DOTALL
        ).strip()
        
        if respuesta_limpia:
            print(f"\n🎙️  Javier: {respuesta_limpia}")
        
        print(f"\n⚠️  [SHELL DETECTADO]:")
        print(f"   \033[1;33m{comando}\033[0m")
        
        ejecutar = modo_autonomo
        if not ejecutar:
            conf = input("¿Autorizar ejecución? [S/n]: ").strip().lower()
            ejecutar = conf in ("", "s", "si", "y", "yes")
        
        if ejecutar:
            print("\n⚙️  [EJECUTANDO EN ZSH...]")
            salida, hubo_error = ejecutar_comando(comando)
            print(f"\n{'─'*50}")
            print(salida)
            print(f"{'─'*50}")
            registrar_comando(mem, orden, comando)
            
            if "flatpak list" in comando and not hubo_error:
                intentar_lanzar_flatpak(mem, salida, orden)
            
            print("\n🤖 [ANALIZANDO RESULTADO...]")
            analisis = analizar_salida(orden, comando, salida)
            print(f"\n🎙️  Javier: {analisis}")
            registrar_turno(mem, "jarvis", analisis)
            return analisis
        else:
            print("\n❌ [SISTEMA]: Ejecución denegada de forma segura.")
            return "Ejecución cancelada."
    
    elif _contiene_escritura(orden):
        nombre_arch, ok = escribir_archivo(orden, respuesta)
        if ok:
            print(f"\n⚙️  [SISTEMA]: Archivo '{nombre_arch}' guardado.")
            print(f"\n🎙️  Javier: Informe plasmado en '{nombre_arch}'.")
        else:
            print(f"\n❌ [SISTEMA]: No pude escribir el archivo '{nombre_arch}'.")
        registrar_turno(mem, "jarvis", respuesta)
        return respuesta
    
    else:
        print(f"\n🎙️  Javier: {respuesta}")
        registrar_turno(mem, "jarvis", respuesta)
        return respuesta


def _manejar_vision(orden: str) -> str:
    """Maneja órdenes de captura de pantalla."""
    import time
    print("\n👁️  [Javier ACTIVANDO VISIÓN — Mueve el cursor al monitor deseado]")
    for _i in range(5, 0, -1):
        print(f"   ⏳ {_i}...", end="\r", flush=True)
        time.sleep(1)
    print("   📸 Capturando...                ")
    pregunta = orden if len(orden) > 10 else "¿Qué ves en esta pantalla? Descríbela en detalle."
    descripcion = ver_pantalla(pregunta)
    print(f"\n🎙️  Javier: {descripcion}")
    return descripcion


def _procesar_comando_memoria(orden: str, mem: dict) -> bool:
    """
    Procesa comandos especiales de gestión de memoria.
    Retorna True si procesó algo, False si debe continuar con flujo normal.
    """
    import json
    import re
    from core.memory.memory_manager import guardar_memoria
    
    o = orden.lower().strip()
    
    if any(x in o for x in ["muéstrame tu memoria", "qué recuerdas", "ver memoria", "mostrar memoria"]):
        print("\n📋 [MEMORIA DE Javier]:")
        print(json.dumps(mem, ensure_ascii=False, indent=2))
        return True
    
    if any(x in o for x in ["borra la conversación", "limpia la memoria conversacional", "olvida la conversación"]):
        mem["conversacion"] = []
        print("\n🎙️  Javier: Historial borrado.")
        return True
    
    if any(x in o for x in ["borra los flatpaks", "olvida los flatpaks", "actualiza flatpaks"]):
        mem["flatpaks"] = {}
        print("\n🎙️  Javier: Caché de Flatpaks reiniciado. Redescubriré las apps al próximo uso.")
        return True
    
    m = re.search(r"(?:recuerda|anota|guarda)\s+(?:que\s+)?(.+)", orden, re.IGNORECASE)
    if m and any(x in o for x in ["recuerda", "anota", "guarda que"]):
        nota = m.group(1).strip()
        mem["preferencias"]["notas"].append(nota)
        mem["preferencias"]["notas"] = mem["preferencias"]["notas"][-10:]
        guardar_memoria(mem)
        print(f"\n🎙️  Javier: Anotado en mi memoria: «{nota}»")
        return True
    
    guardado = False
    
    m = re.search(r"mi nombre es ([A-Za-záéíóúÁÉÍÓÚñÑ]+)", orden, re.IGNORECASE)
    if m:
        nombre = m.group(1).strip().capitalize()
        mem["preferencias"]["nombre_usuario"] = nombre
        guardado = True
        print(f"\n🎙️  Javier: Nombre registrado en memoria permanente: {nombre}.")
    
    m = re.search(r"tengo (\d+) años", orden, re.IGNORECASE)
    if m:
        mem["preferencias"]["edad"] = int(m.group(1))
        guardado = True
        print(f"\n🎙️  Javier: Edad registrada: {m.group(1)} años.")
    
    if guardado:
        guardar_memoria(mem)
        return True
    
    return False
