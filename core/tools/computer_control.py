"""
Control de mouse/teclado para Aether vía ydotool (Wayland/Hyprland-safe).

POR QUÉ ydotool Y NO pynput/xdotool
────────────────────────────────────
pynput y xdotool inyectan eventos vía X11 (XTest). Clientes Wayland
NATIVOS (no XWayland) ignoran esos eventos sintéticos por diseño del
protocolo — es una protección de seguridad, no un bug a parchear.

ydotool inyecta a nivel de kernel (/dev/uinput), el mismo mecanismo que
usaría un dispositivo HID físico. Funciona sin importar si la ventana
activa es XWayland o Wayland nativo.

Requiere:
  - paquete `ydotool` instalado
  - daemon `ydotoold` corriendo
  - usuario con acceso a /dev/uinput

Todas las funciones devuelven:
    (mensaje: str, hubo_error: bool)
"""

import subprocess


# Códigos de botón de ydotool
_BOTONES = {
    "left":   "0xC0",
    "right":  "0xC1",
    "middle": "0xC2",
}


# Scroll usando eventos REL_WHEEL
_SCROLL = {
    "up": "0x400",
    "down": "0x401",
}


# Códigos básicos de teclado Linux para ydotool
_TECLAS = {
    "ENTER": "28",
    "ESC": "1",
    "ESCAPE": "1",
    "SPACE": "57",
    "TAB": "15",
    "BACKSPACE": "14",
    "DELETE": "111",
    "UP": "103",
    "DOWN": "108",
    "LEFT": "105",
    "RIGHT": "106",
}


def _run(cmd: list[str], timeout: int = 5) -> tuple[str, bool]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if r.returncode != 0:
            detalle = r.stderr.strip() or f"ydotool salió con código {r.returncode}"
            return detalle, True

        return "OK", False

    except FileNotFoundError:
        return "ydotool no está instalado (pacman -S ydotool).", True

    except subprocess.TimeoutExpired:
        return "ydotool no respondió a tiempo — ¿ydotoold está corriendo?", True

    except Exception as e:
        return f"Error inesperado ejecutando ydotool: {e}", True


def _validar_coordenadas(x: int, y: int) -> bool:
    """
    Validación básica para evitar coordenadas inválidas.
    El límite superior real depende del monitor.
    """
    return x >= 0 and y >= 0


def mover_mouse(x: int, y: int) -> tuple[str, bool]:
    """Mueve el cursor a coordenadas absolutas de pantalla."""
    x = int(x)
    y = int(y)

    if not _validar_coordenadas(x, y):
        return "Coordenadas inválidas.", True

    return _run([
        "ydotool",
        "mousemove",
        "-a",
        "-x",
        str(x),
        "-y",
        str(y)
    ])


def click_mouse(boton: str = "left") -> tuple[str, bool]:
    """Click en la posición actual del cursor."""
    codigo = _BOTONES.get(
        boton.lower(),
        _BOTONES["left"]
    )

    return _run([
        "ydotool",
        "click",
        codigo
    ])


def click_en(x: int, y: int, boton: str = "left") -> tuple[str, bool]:
    """Mueve el cursor a una posición y hace click."""
    msg, err = mover_mouse(x, y)

    if err:
        return f"Fallo al mover el mouse: {msg}", True

    return click_mouse(boton)


def escribir_texto(texto: str) -> tuple[str, bool]:
    """Escribe texto como teclado."""
    return _run([
        "ydotool",
        "type",
        texto
    ], timeout=10)


def presionar_tecla(tecla: str) -> tuple[str, bool]:
    """
    Presiona una tecla simple.

    Ej:
        ENTER
        ESC
        SPACE
    """

    tecla_normalizada = tecla.upper()

    codigo = _TECLAS.get(
        tecla_normalizada,
        tecla
    )

    return _run([
        "ydotool",
        "key",
        f"{codigo}:1",
        f"{codigo}:0"
    ])


def scroll(cantidad: int) -> tuple[str, bool]:
    """
    Scroll vertical.

    cantidad > 0 = arriba
    cantidad < 0 = abajo
    """

    if cantidad == 0:
        return "OK", False

    codigo = (
        _SCROLL["up"]
        if cantidad > 0
        else _SCROLL["down"]
    )

    pasos = abs(int(cantidad))

    ultimo_error = None

    for _ in range(pasos):
        msg, err = _run([
            "ydotool",
            "click",
            codigo
        ])

        if err:
            ultimo_error = msg
            break

    if ultimo_error:
        return ultimo_error, True

    return "OK", False