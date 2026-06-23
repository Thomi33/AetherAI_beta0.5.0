# core/utils/intent_utils.py

from core.config.settings import (
    PALABRAS_CLAVE_ESCRITURA,
    PALABRAS_CLAVE_VISION,
    PALABRAS_CLAVE_LANZAR,
    PALABRAS_CLAVE_WEB,
)


def contiene_escritura(orden: str) -> bool:
    return bool(
        set(orden.lower().split()) &
        PALABRAS_CLAVE_ESCRITURA
    )


def necesita_web(orden: str) -> bool:
    o = orden.lower()
    return any(x in o for x in PALABRAS_CLAVE_WEB)


def contiene_vision(orden: str) -> bool:
    if necesita_web(orden):
        return False

    return any(
        x in orden.lower()
        for x in PALABRAS_CLAVE_VISION
    )


def contiene_lanzar(orden: str) -> bool:
    return bool(
        set(orden.lower().split()) &
        PALABRAS_CLAVE_LANZAR
    )