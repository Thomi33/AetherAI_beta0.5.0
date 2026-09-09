"""
Herramienta de sistema de archivos de primera clase para Aether.

MOTIVO (auditoría 2026-09-06, punto 3 de opinion.md):
node_codigo solo materializa el PRIMER bloque de código de la respuesta del
LLM (bloques[0]) y file_write (file_writer.py) resuelve la ruta adivinando
un token con regex sobre texto libre -- ninguno de los dos soporta "crear un
proyecto con N archivos nombrados" ni leer/listar el filesystem. Este módulo
cubre ese hueco con operaciones explícitas (path + content), consistentes
con el agent loop de tool calling nativo (node_agent_loop en graph_nodes.py):
el modelo pasa argumentos estructurados, no hay que adivinarlos de texto.

No reemplaza:
- `codigo` (core/tools/... vía node_codigo): sigue siendo la vía para
  generar+EJECUTAR un script. Si además hay que guardarlo en una ruta con
  nombre, node_codigo ya resuelve eso (_resolver_ruta_destino_codigo).
- `file_write` legacy (file_writer.py / node_file_write): sigue existiendo
  para el fast-path determinista de plan_executor ("guardá lo que buscaste"
  con ruta inferida de la orden en texto libre). No se toca acá.

Todas las funciones devuelven (dato, exito: bool, error_msg: str) para que
los nodos que las envuelven en graph_nodes.py puedan armar el dict de
estado (final_response / error_activo) de forma uniforme.
"""
from __future__ import annotations

import os

from core.config import settings as _settings

from core.config.settings import BASE_AETHER


def _base_trabajo() -> str:
    try:
        rt = _settings.RUTA_TRABAJO
        if rt.is_dir():
            return str(rt)
    except Exception:
        pass
    # Fallback legacy: la data del agente vive en ~/Aether (BASE_AETHER).
    return str(BASE_AETHER)


def _resolver_ruta(path: str) -> str:
    """
    Resuelve una ruta a absoluta:
    - absoluta o con `~` → se respeta (expandiendo `~`)
    - relativa → se resuelve dentro de RUTA_TRABAJO (el directorio desde
      el que se invocó a Aether: ~/Documents, ~/Proyecto, ...).
      Fallback legacy: BASE_AETHER (~/Aether) si RUTA_TRABAJO no existe.
    """
    if not path or not str(path).strip():
        raise ValueError("Ruta vacía.")
    p = os.path.expanduser(str(path).strip())
    if not os.path.isabs(p):
        p = os.path.join(_base_trabajo(), p)
    return os.path.abspath(p)


def escribir_archivo_fs(path: str, content: str) -> tuple[str, bool, str]:
    """Escribe UN archivo en `path` (creando directorios intermedios).
    Retorna (ruta_absoluta, exito, error_msg)."""
    try:
        destino = _resolver_ruta(path)
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            f.write(content if content is not None else "")
        return destino, True, ""
    except Exception as e:
        return str(path), False, str(e)


def _rollback(rutas: list[str]) -> None:
    """Borra los archivos ya escritos en un batch que falló a mitad de camino."""
    for r in rutas:
        try:
            if os.path.isfile(r):
                os.remove(r)
        except Exception:
            pass


def escribir_archivos_fs(files: list[dict]) -> tuple[list[str], bool, str]:
    """
    Escribe VARIOS archivos de forma atómica best-effort: si alguno falla,
    borra los que ya se habían escrito EN ESTA MISMA LLAMADA antes de
    reportar el error, para no dejar un proyecto a medio escribir.

    `files`: lista de {"path": <str>, "content": <str>} (también acepta
    "filename" como alias de "path" para compatibilidad con el schema de
    otras tools).

    Retorna (rutas_escritas, exito, error_msg).
    """
    if not isinstance(files, list) or not files:
        return [], False, "'files' debe ser una lista no vacía de {path, content}."

    escritas: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            _rollback(escritas)
            return [], False, f"Cada elemento de 'files' debe ser un objeto, recibido: {item!r}"
        path = item.get("path") or item.get("filename")
        content = item.get("content", "")
        if not path:
            _rollback(escritas)
            return [], False, f"Falta 'path'/'filename' en uno de los archivos: {item!r}"
        destino, ok, err = escribir_archivo_fs(path, content)
        if not ok:
            _rollback(escritas)
            return [], False, f"Falló al escribir '{path}': {err}"
        escritas.append(destino)

    return escritas, True, ""


def leer_archivo_fs(path: str) -> tuple[str, bool, str]:
    """Lee un archivo de texto. Retorna (contenido, exito, error_msg)."""
    try:
        destino = _resolver_ruta(path)
        with open(destino, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), True, ""
    except Exception as e:
        return "", False, str(e)


def crear_directorio_fs(path: str) -> tuple[str, bool, str]:
    """Crea un directorio (y sus padres si hacen falta). Retorna (ruta_abs, exito, error_msg)."""
    try:
        destino = _resolver_ruta(path)
        os.makedirs(destino, exist_ok=True)
        return destino, True, ""
    except Exception as e:
        return str(path), False, str(e)


def listar_directorio_fs(path: str) -> tuple[str, bool, str]:
    """Lista el contenido (no recursivo) de un directorio. Retorna (listado, exito, error_msg)."""
    try:
        destino = _resolver_ruta(path)
        if not os.path.isdir(destino):
            return "", False, f"'{destino}' no es un directorio."
        entradas = sorted(os.listdir(destino))
        lineas = []
        for nombre in entradas:
            ruta_completa = os.path.join(destino, nombre)
            tipo = "DIR " if os.path.isdir(ruta_completa) else "FILE"
            lineas.append(f"[{tipo}] {nombre}")
        return ("\n".join(lineas) if lineas else "(directorio vacío)"), True, ""
    except Exception as e:
        return "", False, str(e)
