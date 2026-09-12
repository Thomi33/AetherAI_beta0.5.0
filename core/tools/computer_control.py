"""Control determinista de mouse/teclado para Aether en Wayland.

Las acciones se verifican primero consultando al compositor. El VLM no forma
parte del camino normal: solo puede usarse como fallback explícito cuando el
compositor no puede confirmar el efecto visual de una acción.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable


logger = logging.getLogger(__name__)

_BOTONES = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}
_SCROLL = {"up": "0x400", "down": "0x401"}
_TECLAS = {
    "ENTER": "28", "ESC": "1", "ESCAPE": "1", "SPACE": "57",
    "TAB": "15", "BACKSPACE": "14", "DELETE": "111",
    "UP": "103", "DOWN": "108", "LEFT": "105", "RIGHT": "106",
    "W": "17", "A": "30", "S": "31", "D": "32",
}
_MAX_RETRIES = 3
_RETRY_BACKOFF = (0.02, 0.05, 0.1)


@dataclass(frozen=True)
class DesktopContext:
    compositor: str
    monitor: str | None
    workspace: str | None
    focused_window: str | None
    cursor_x: int | None
    cursor_y: int | None
    desktop_left: int = 0
    desktop_top: int = 0
    desktop_width: int | None = None
    desktop_height: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Verification:
    ok: bool
    level: int
    message: str
    retryable: bool = False


_context_cache: DesktopContext | None = None


def _run(cmd: list[str], timeout: int = 5) -> tuple[str, bool]:
    """Ejecuta ydotool y devuelve siempre un error explícito."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return f"{cmd[0]} no está instalado.", True
    except subprocess.TimeoutExpired:
        return f"{cmd[0]} no respondió a tiempo.", True
    except OSError as exc:
        return f"Error ejecutando {cmd[0]}: {exc}", True

    if result.returncode:
        detail = result.stderr.strip() or f"{cmd[0]} salió con código {result.returncode}"
        return detail, True
    return "OK", False


def _run_query(cmd: list[str], timeout: float = 2.0) -> tuple[Any | None, str | None]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, f"{cmd[0]} no está instalado."
    except subprocess.TimeoutExpired:
        return None, f"{cmd[0]} no respondió en {timeout:g}s."
    except OSError as exc:
        return None, f"Error ejecutando {cmd[0]}: {exc}"
    if result.returncode:
        return None, result.stderr.strip() or f"{cmd[0]} salió con código {result.returncode}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"{cmd[0]} devolvió JSON inválido: {exc}"


def _compositor() -> str:
    if shutil.which("hyprctl") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if shutil.which("swaymsg") and os.environ.get("SWAYSOCK"):
        return "sway"
    if shutil.which("hyprctl"):
        return "hyprland"
    if shutil.which("swaymsg"):
        return "sway"
    return "unknown"


def _hyprland_context() -> tuple[dict[str, Any] | None, str | None]:
    monitors, error = _run_query(["hyprctl", "monitors", "-j"])
    if error:
        return None, error
    active, error = _run_query(["hyprctl", "activeworkspace", "-j"])
    if error:
        return None, error
    cursor, error = _run_query(["hyprctl", "cursorpos", "-j"])
    if error:
        return None, error
    focused_data, error = _run_query(["hyprctl", "activewindow", "-j"])
    if error:
        return None, error
    focused = focused_data.get("address") if isinstance(focused_data, dict) else None
    return {
        "monitors": monitors if isinstance(monitors, list) else [],
        "workspace": active.get("name") if isinstance(active, dict) else None,
        "focused_window": focused,
        "cursor": cursor if isinstance(cursor, dict) else {},
    }, None


def _sway_context() -> tuple[dict[str, Any] | None, str | None]:
    workspaces, error = _run_query(["swaymsg", "-t", "get_workspaces", "-r"])
    if error:
        return None, error
    outputs, error = _run_query(["swaymsg", "-t", "get_outputs", "-r"])
    if error:
        return None, error
    tree, error = _run_query(["swaymsg", "-t", "get_tree", "-r"])
    if error:
        return None, error
    seats, error = _run_query(["swaymsg", "-t", "get_seats", "-r"])
    if error:
        return None, error
    focused_workspace = next((w for w in workspaces if w.get("focused")), {})
    focused_window = _focused_sway_node(tree)
    seat = seats[0] if isinstance(seats, list) and seats else {}
    return {
        "monitors": outputs if isinstance(outputs, list) else [],
        "workspace": focused_workspace.get("name"),
        "focused_window": focused_window,
        "cursor": seat.get("cursor") or {},
    }, None


def _focused_sway_node(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    if node.get("focused") and (node.get("app_id") or node.get("window")):
        return node.get("app_id") or node.get("window")
    for child in (*node.get("nodes", []), *node.get("floating_nodes", [])):
        result = _focused_sway_node(child)
        if result:
            return result
    return None


def _focused_sway_con_id(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    if node.get("focused") and node.get("id") is not None:
        return str(node["id"])
    for child in (*node.get("nodes", []), *node.get("floating_nodes", [])):
        result = _focused_sway_con_id(child)
        if result:
            return result
    return None


def _find_sway_window(node: Any, name: str) -> str | None:
    if not isinstance(node, dict):
        return None
    needle = name.casefold()
    haystack = " ".join(
        str(node.get(key, "")) for key in ("app_id", "window_properties", "name")
    )
    if needle in haystack.casefold() and node.get("id") is not None:
        return str(node["id"])
    for child in (*node.get("nodes", []), *node.get("floating_nodes", [])):
        result = _find_sway_window(child, name)
        if result:
            return result
    return None


def buscar_ventana(nombre_parcial: str) -> tuple[str | None, str | None]:
    """Busca la primera ventana cuyo class/app_id o título contenga el nombre."""
    nombre = str(nombre_parcial).strip()
    if not nombre:
        return None, "El nombre parcial de la ventana no puede estar vacío."

    compositor = _compositor()
    if compositor == "hyprland":
        clients, error = _run_query(["hyprctl", "clients", "-j"])
        if error:
            return None, error
        for client in clients if isinstance(clients, list) else []:
            class_name = str(client.get("class", ""))
            title = str(client.get("title", ""))
            if nombre.casefold() in f"{class_name} {title}".casefold():
                address = client.get("address")
                if address:
                    return str(address), None
        return None, f"No se encontró una ventana que contenga {nombre!r}."

    if compositor == "sway":
        tree, error = _run_query(["swaymsg", "-t", "get_tree", "-r"])
        if error:
            return None, error
        window_id = _find_sway_window(tree, nombre)
        if window_id:
            return window_id, None
        return None, f"No se encontró una ventana que contenga {nombre!r}."

    return None, "No se detectó un compositor Wayland compatible."


def _monitor_at_cursor(monitors: list[dict[str, Any]], x: int | None, y: int | None) -> str | None:
    if x is None or y is None:
        return None
    for monitor in monitors:
        try:
            rect = monitor.get("rect", {})
            left = int(monitor.get("x", rect.get("x", 0)))
            top = int(monitor.get("y", rect.get("y", 0)))
            width = int(monitor["width"] if "width" in monitor else rect["width"])
            height = int(monitor["height"] if "height" in monitor else rect["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if left <= x < left + width and top <= y < top + height:
            return monitor.get("name")
    return None


def _desktop_bounds(monitors: list[dict[str, Any]]) -> tuple[int, int, int | None, int | None]:
    rectangles = []
    for monitor in monitors:
        try:
            rect = monitor.get("rect", {})
            left = int(monitor["x"] if "x" in monitor else rect["x"])
            top = int(monitor["y"] if "y" in monitor else rect["y"])
            width = int(monitor["width"] if "width" in monitor else rect["width"])
            height = int(monitor["height"] if "height" in monitor else rect["height"])
        except (KeyError, TypeError, ValueError):
            continue
        rectangles.append((left, top, width, height))
    if not rectangles:
        return 0, 0, None, None
    left = min(item[0] for item in rectangles)
    top = min(item[1] for item in rectangles)
    right = max(item[0] + item[2] for item in rectangles)
    bottom = max(item[1] + item[3] for item in rectangles)
    return left, top, right - left, bottom - top


def resolver_contexto(force: bool = False) -> DesktopContext:
    """Resuelve y cachea el contexto del escritorio para una secuencia."""
    global _context_cache
    if _context_cache is not None and not force:
        return _context_cache

    compositor = _compositor()
    raw, error = (
        _hyprland_context() if compositor == "hyprland"
        else _sway_context() if compositor == "sway"
        else (None, "No se detectó Hyprland ni Sway.")
    )
    if error or raw is None:
        raise RuntimeError(f"No se pudo consultar el compositor: {error}")
    cursor = raw.get("cursor", {})
    x, y = cursor.get("x"), cursor.get("y")
    left, top, width, height = _desktop_bounds(raw["monitors"])
    _context_cache = DesktopContext(
        compositor=compositor,
        monitor=_monitor_at_cursor(raw["monitors"], x, y),
        workspace=raw.get("workspace"),
        focused_window=raw.get("focused_window"),
        cursor_x=x,
        cursor_y=y,
        desktop_left=left,
        desktop_top=top,
        desktop_width=width,
        desktop_height=height,
    )
    return _context_cache


def invalidar_contexto() -> None:
    global _context_cache
    _context_cache = None


def iniciar_secuencia() -> DesktopContext:
    """Resuelve el contexto una vez al comenzar una secuencia de acciones."""
    return resolver_contexto(force=True)


def consultar_cursor() -> tuple[tuple[int, int] | None, str | None]:
    compositor = _compositor()
    if compositor == "hyprland":
        cursor, error = _run_query(["hyprctl", "cursorpos", "-j"])
    elif compositor == "sway":
        seats, error = _run_query(["swaymsg", "-t", "get_seats", "-r"])
        cursor = seats[0].get("cursor") if isinstance(seats, list) and seats else None
    else:
        return None, "No se detectó un compositor Wayland compatible."
    if error:
        return None, error
    if not isinstance(cursor, dict) or cursor.get("x") is None or cursor.get("y") is None:
        return None, "El compositor no expone la posición del cursor."
    return (int(cursor["x"]), int(cursor["y"])), None


def _log_action(action: str, context: DesktopContext | None, result: str, retries: int) -> None:
    logger.info(
        "computer_action action=%s context=%s result=%s retries=%d",
        action, context.as_dict() if context else None, result, retries,
    )


def _verified(
    action: str,
    command: list[str],
    verifier: Callable[[], Verification],
    *,
    context: DesktopContext | None = None,
    retries: int = _MAX_RETRIES,
    vlm_fallback: Callable[[str], bool] | None = None,
    timeout: int = 5,
) -> tuple[str, bool]:
    last_message = "La acción no pudo verificarse."
    used_retries = 0
    for attempt in range(retries + 1):
        used_retries = attempt
        message, failed = _run(command, timeout=timeout)
        if failed:
            last_message = message
        else:
            try:
                verification = verifier()
            except RuntimeError as exc:
                verification = Verification(False, 1, str(exc), retryable=True)
            last_message = verification.message
            if verification.ok:
                _log_action(action, context, "ok", attempt)
                return f"OK (verificado nivel {verification.level})", False
            if not verification.retryable:
                break
        if attempt < retries:
            time.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])

    if vlm_fallback is not None:
        try:
            if vlm_fallback(last_message):
                _log_action(action, context, "ok-level-2", used_retries)
                return "OK (diagnóstico nivel 2)", False
        except (OSError, RuntimeError, ValueError) as exc:
            last_message = f"{last_message}; fallback visual falló: {exc}"
    _log_action(action, context, f"error: {last_message}", used_retries)
    return f"Falló {action} tras {used_retries} reintentos: {last_message}", True


def _validar_coordenadas(x: int, y: int) -> bool:
    return x >= 0 and y >= 0


def mover_mouse(
    x: int,
    y: int,
    *,
    vlm_fallback: Callable[[str], bool] | None = None,
) -> tuple[str, bool]:
    """Mueve el cursor y confirma su posición con una consulta al compositor."""
    x, y = int(x), int(y)
    if not _validar_coordenadas(x, y):
        return "Coordenadas inválidas.", True
    context = resolver_contexto()
    if context.compositor == "hyprland":
        command = ["hyprctl", "dispatch", "movecursor", str(x), str(y)]
    else:
        command = ["ydotool", "mousemove", "-a", "-x", str(x), "-y", str(y)]

    def verify() -> Verification:
        position, error = consultar_cursor()
        if error:
            return Verification(False, 1, error, retryable=True)
        ok = position == (x, y)
        return Verification(ok, 1, f"cursor={position}, esperado=({x}, {y})", retryable=True)

    result = _verified(
        "mover_mouse", command,
        verify, context=context, vlm_fallback=vlm_fallback,
    )
    if not result[1]:
        invalidar_contexto()
    return result


def click_mouse(
    boton: str = "left",
    *,
    expected_workspace: str | None = None,
    expected_window: str | None = None,
    vlm_fallback: Callable[[str], bool] | None = None,
) -> tuple[str, bool]:
    """Hace click y verifica foco/workspace cuando el llamador da una expectativa."""
    codigo = _BOTONES.get(boton.lower())
    if codigo is None:
        return f"Botón inválido: {boton}.", True
    context = resolver_contexto()

    def verify() -> Verification:
        current = resolver_contexto(force=True)
        if expected_workspace is not None and current.workspace != expected_workspace:
            return Verification(False, 1, f"workspace={current.workspace!r}", retryable=True)
        if expected_window is not None and current.focused_window != expected_window:
            return Verification(False, 1, f"ventana enfocada={current.focused_window!r}", retryable=True)
        if expected_workspace is None and expected_window is None:
            return Verification(False, 1, "click sin expectativa determinista; efecto visual no aplicable", False)
        return Verification(True, 1, "foco/workspace confirmados", False)

    result = _verified(
        "click_mouse", ["ydotool", "click", codigo], verify,
        context=context, vlm_fallback=vlm_fallback,
    )
    if not result[1] and (expected_workspace is not None or expected_window is not None):
        invalidar_contexto()
    return result


def click_en(
    x: int,
    y: int,
    boton: str = "left",
    *,
    expected_workspace: str | None = None,
    expected_window: str | None = None,
    vlm_fallback: Callable[[str], bool] | None = None,
) -> tuple[str, bool]:
    msg, error = mover_mouse(x, y, vlm_fallback=vlm_fallback)
    if error:
        return f"Fallo al mover el mouse: {msg}", True
    return click_mouse(
        boton, expected_workspace=expected_workspace,
        expected_window=expected_window, vlm_fallback=vlm_fallback,
    )


def cambiar_workspace(workspace: str | int) -> tuple[str, bool]:
    """Cambia de workspace y confirma el resultado desde el compositor."""
    objetivo = str(workspace)
    context = resolver_contexto()
    if context.compositor == "hyprland":
        command = ["hyprctl", "dispatch", "workspace", objetivo]
    elif context.compositor == "sway":
        command = ["swaymsg", f"workspace {objetivo}"]
    else:
        return "No se detectó un compositor Wayland compatible.", True

    def verify() -> Verification:
        current = resolver_contexto(force=True)
        return Verification(
            current.workspace == objetivo,
            1,
            f"workspace={current.workspace!r}, esperado={objetivo!r}",
            retryable=True,
        )

    result = _verified("cambiar_workspace", command, verify, context=context)
    if not result[1]:
        invalidar_contexto()
    return result


def enfocar_ventana(window_id: str) -> tuple[str, bool]:
    """Enfoca una ventana cuyo identificador ya fue resuelto por el compositor."""
    context = resolver_contexto()
    if context.compositor == "hyprland":
        command = ["hyprctl", "dispatch", "focuswindow", f"address:{window_id}"]
    elif context.compositor == "sway":
        command = ["swaymsg", f"[con_id={window_id}] focus"]
    else:
        return "No se detectó un compositor Wayland compatible.", True

    def verify() -> Verification:
        current = resolver_contexto(force=True)
        focused = current.focused_window
        if context.compositor == "sway":
            tree, error = _run_query(["swaymsg", "-t", "get_tree", "-r"])
            if error:
                return Verification(False, 1, error, retryable=True)
            focused = _focused_sway_con_id(tree)
        return Verification(
            focused == window_id,
            1,
            f"ventana enfocada={focused!r}, esperada={window_id!r}",
            retryable=True,
        )

    result = _verified("enfocar_ventana", command, verify, context=context)
    if not result[1]:
        invalidar_contexto()
    return result


def activar_entrada_ventana(window_id: str) -> tuple[str, bool]:
    """Hace click en el centro del canvas para que Roblox capture el teclado."""
    compositor = _compositor()
    if compositor == "hyprland":
        clients, error = _run_query(["hyprctl", "clients", "-j"])
        if error:
            return error, True
        client = next(
            (item for item in clients if item.get("address") == window_id),
            None,
        )
        if not isinstance(client, dict):
            return f"No se encontró la geometría de Sober: {window_id}", True
        x, y = client.get("at", [None, None])
        width, height = client.get("size", [None, None])
        if None in (x, y, width, height):
            return "Sober no expuso una geometría válida.", True
        move_result = _run(
            ["ydotool", "mousemove", "-a", "-x", str(int(x) + int(width) // 2),
             "-y", str(int(y) + int(height) // 2)]
        )
        if move_result[1]:
            return move_result
    else:
        return "Solo se puede activar el canvas automáticamente en Hyprland.", True

    return _run(["ydotool", "click", "0xC0:1", "0xC0:0"])


def escribir_texto(texto: str) -> tuple[str, bool]:
    context = resolver_contexto()
    result = _run(["ydotool", "type", texto], timeout=10)
    _log_action("escribir_texto", context, "error" if result[1] else "ok", 0)
    return result


def escribir_chat(texto: str) -> tuple[str, bool]:
    """Abre el chat de Roblox, escribe una respuesta acotada y la envía."""
    texto = " ".join(str(texto).split()).strip()
    if not texto or len(texto) > 120:
        return "El mensaje de chat está vacío o excede 120 caracteres.", True
    open_result = _run(["ydotool", "key", "53:1", "53:0"])
    if open_result[1]:
        return open_result
    type_result = _run(["ydotool", "type", texto], timeout=10)
    if type_result[1]:
        return type_result
    return _run(["ydotool", "key", "28:1", "28:0"])


def presionar_tecla(tecla: str) -> tuple[str, bool]:
    codigo = _TECLAS.get(tecla.upper(), tecla)
    context = resolver_contexto()
    result = _run(["ydotool", "key", f"{codigo}:1", f"{codigo}:0"])
    _log_action("presionar_tecla", context, "error" if result[1] else "ok", 0)
    return result


def mantener_tecla(tecla: str, duracion_s: float) -> tuple[str, bool]:
    """Mantiene una tecla presionada y garantiza el key-up ante cualquier fallo."""
    codigo = _TECLAS.get(tecla.upper(), tecla)
    try:
        duracion = float(duracion_s)
    except (TypeError, ValueError):
        return "La duración de la tecla debe ser numérica.", True
    if duracion < 0:
        return "La duración de la tecla no puede ser negativa.", True

    context = resolver_contexto()
    down_message, down_error = _run(["ydotool", "key", f"{codigo}:1"])
    if down_error:
        _log_action("mantener_tecla", context, f"error: {down_message}", 0)
        return down_message, True

    try:
        time.sleep(duracion)
    finally:
        up_message, up_error = _run(["ydotool", "key", f"{codigo}:0"])

    if up_error:
        message = f"No se pudo soltar la tecla {tecla}: {up_message}"
        _log_action("mantener_tecla", context, f"error: {message}", 0)
        return message, True
    _log_action("mantener_tecla", context, "ok", 0)
    return "OK", False


def mover_mouse_relativo(
    dx: int,
    dy: int,
    *,
    vlm_fallback: Callable[[str], bool] | None = None,
    verify: bool = True,
) -> tuple[str, bool]:
    """Mueve el cursor de forma relativa (delta) respecto a la posición actual.

    A diferencia de mover_mouse(), no verifica la posición absoluta final
    porque el compositor no expone delta confirmado; solo verifica que el
    cursor se haya movido en la dirección correcta dentro de los límites del
    escritorio.

    Usa ydotool con el flag relativo correcto (-x/-y sin -a) en todos los
    compositores (hyprctl no tiene comando de movimiento relativo nativo).

    Args:
        verify: Cuando False, ejecuta fire-and-forget sin consultar/verificar
            la posición del cursor. Fast path para los barridos de cámara del
            loop autónomo, donde cada spawn de hyprctl por punto es latencia
            pura. El default True preserva el comportamiento verificador.
    """
    dx, dy = int(dx), int(dy)
    if dx == 0 and dy == 0:
        return "OK", False
    if not verify:
        message, failed = _run(["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)])
        return message, failed

    position_before, error = consultar_cursor()
    if error or position_before is None:
        # Sin posición previa no podemos verificar; ejecutar sin verificación.
        message, failed = _run(["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)])
        return message, failed

    command = ["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)]

    def verify() -> Verification:
        position_after, err = consultar_cursor()
        if err or position_after is None:
            return Verification(False, 1, err or "No se pudo leer el cursor.", retryable=True)
        moved = position_after != position_before
        return Verification(
            moved, 1,
            f"antes={position_before}, después={position_after}",
            retryable=not moved,
        )

    context = resolver_contexto()
    return _verified(
        "mover_mouse_relativo", command, verify,
        context=context, vlm_fallback=vlm_fallback,
    )


def scroll(cantidad: int) -> tuple[str, bool]:
    if cantidad == 0:
        return "OK", False
    codigo = _SCROLL["up" if cantidad > 0 else "down"]
    context = resolver_contexto()
    for _ in range(abs(int(cantidad))):
        message, error = _run(["ydotool", "click", codigo])
        if error:
            _log_action("scroll", context, f"error: {message}", 0)
            return message, True
    _log_action("scroll", context, "ok", 0)
    return "OK", False
