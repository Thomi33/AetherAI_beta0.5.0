"""
Escritura segura de archivos con auto-detección de extensión.
"""
import re


_EXT_MAP = {"txt": ".txt", "json": ".json", "md": ".md",
            "sh": ".sh", "py": ".py", "html": ".html"}


def _detectar_extension(orden: str) -> str:
    """Detecta la extensión de archivo deseada desde la orden."""
    for ext, sufijo in _EXT_MAP.items():
        if ext in orden.lower():
            return sufijo
    return ".txt"


def escribir_archivo(orden: str, contenido: str) -> tuple[str, bool]:
    """
    Escribe contenido a archivo.
    Retorna (nombre_archivo, exito).
    """
    m = re.search(r"[\w_\-]+\.(?:txt|md|json|sh|py|html)", orden)
    nombre = m.group(0) if m else f"reporte_jarvis{_detectar_extension(orden)}"
    contenido_limpio = re.sub(
        r"```[\w]*\n(.*?)\n```", r"\1", contenido, flags=re.DOTALL
    ).strip()
    try:
        with open(nombre, "w", encoding="utf-8") as f:
            f.write(contenido_limpio)
        return nombre, True
    except Exception:
        return nombre, False
