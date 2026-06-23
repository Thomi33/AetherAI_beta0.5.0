from typing import Any
from typing_extensions import TypedDict

IntentType = str

MAX_INTENTOS_DEFAULT = 3

MAX_INTENTOS_POR_CONTEXTO = {
    "launch": 5,
    "shell":  3,
    "codigo": 3,
}

class AetherState(TypedDict):
    orden:          str
    mem:            dict[str, Any]
    modo_autonomo:  bool
    tokens:         list
    terminado:      bool
    ruta:           str
    respuesta:      str | None

    comando_shell:  str | None
    salida_shell:   str
    hubo_error:     bool

    error_contexto:        str
    error_mensaje:         str
    error_cmd:             str
    error_codigo_original: str
    error_archivo:         str
    error_intento:         int
    error_max_intentos:    int
    error_autorizado:      bool
    error_fix_propuesto:   str
    error_fix_diff:        str
    error_fix_fuente:      str