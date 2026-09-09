"""
Escritura segura de archivos con auto-detección de extensión y ruta.
"""
import os
import re

from core.config import settings as _settings

from core.config.settings import BASE_AETHER


def _base_trabajo() -> str:
    try:
        rt = _settings.RUTA_TRABAJO
        if rt.is_dir():
            return str(rt)
    except Exception:
        pass
    return str(BASE_AETHER)


_EXT_MAP = {"txt": ".txt", "json": ".json", "md": ".md",
            "sh": ".sh", "py": ".py", "html": ".html"}

# Token de ruta/archivo: admite ~, /, subdirectorios y una extensión conocida.
_RE_RUTA = re.compile(r"[~\w./\-]+\.(?:txt|md|json|sh|py|html|csv|log)")


def _detectar_extension(orden: str) -> str:
    """Detecta la extensión de archivo deseada desde la orden."""
    for ext, sufijo in _EXT_MAP.items():
        if ext in orden.lower():
            return sufijo
    return ".txt"


def _resolver_destino(orden: str) -> str:
    """
    Resuelve la ruta ABSOLUTA donde escribir, a partir de la orden:

    - Si la orden trae una ruta ABSOLUTA o con `~` (p.ej. ~/Documentos/x.txt,
      /tmp/y.md) → se respeta (expandiendo `~`).
    - Si trae un nombre/relativo (p.ej. precio.txt, sub/p.txt) → va dentro de
      RUTA_TRABAJO (el directorio desde el que se invocó a Aether).
    - Si no trae nombre → nombre por defecto en RUTA_TRABAJO.

    Solo se busca el nombre en la parte de la orden ANTERIOR al bloque
    "[CONTEXTO DE PASOS PREVIOS]" para no confundir una URL de los resultados
    web con el nombre del archivo.
    """
    cabecera = orden.split("[CONTEXTO DE PASOS PREVIOS]")[0]
    m = _RE_RUTA.search(cabecera)
    candidato = m.group(0) if m else f"reporte_jarvis{_detectar_extension(cabecera)}"

    candidato = os.path.expanduser(candidato)
    if os.path.isabs(candidato):
        destino = candidato
    else:
        destino = os.path.join(_base_trabajo(), candidato)
    return os.path.abspath(destino)


def escribir_archivo(orden: str, contenido: str) -> tuple[str, bool]:
    """
    Escribe `contenido` en el archivo resuelto desde `orden`.

    Retorna (ruta_absoluta, exito). Crea los directorios intermedios si hace
    falta. La ruta retornada es SIEMPRE absoluta para que el usuario sepa
    exactamente dónde quedó el archivo.
    """
    destino = _resolver_destino(orden)
    contenido_limpio = re.sub(
        r"```[\w]*\n(.*?)\n```", r"\1", contenido, flags=re.DOTALL
    ).strip()
    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            f.write(contenido_limpio)
        return destino, True
    except Exception:
        return destino, False
