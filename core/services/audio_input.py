"""Resolución explícita de la entrada de audio de Aether.

ALSA y PyAudio numeran las placas de forma independiente y esos índices pueden
cambiar al reiniciar. En lugar de depender del dispositivo por defecto, ambos
consumidores buscan una coincidencia estable en el nombre visible de la placa.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

DEFAULT_AUDIO_INPUT_MATCH = "AudioBox USB 96"


class AudioInputNoDisponible(RuntimeError):
    """No se encontró una entrada de audio que coincida con la configuración."""


def obtener_audio_input_match() -> str:
    """Lee el selector de configuración sin obligar a callers externos a usarla."""
    try:
        from core.config.config_manager import get_config_manager

        value = get_config_manager().get("AUDIO_INPUT_MATCH", DEFAULT_AUDIO_INPUT_MATCH)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return DEFAULT_AUDIO_INPUT_MATCH


def _normalizar_nombre(value: str) -> str:
    """Normaliza separadores de ALSA/PulseAudio para comparar nombres."""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def resolver_fuente_pulse(match: str | None = None, fuente_default: str | None = None) -> str:
    """Valida que la fuente Pulse/PipeWire por defecto sea la AudioBox.

    Capturar vía ``pulse`` permite que F2 y el daemon de wake word compartan
    la misma interfaz. Abrir ``plughw`` directamente bloquea el hardware para
    el otro proceso, por lo que aquí nunca devolvemos la placa ALSA cruda.
    """
    objetivo = match or obtener_audio_input_match()
    if fuente_default is None:
        try:
            result = subprocess.run(
                ["pactl", "get-default-source"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise AudioInputNoDisponible("No se encontró 'pactl'; PipeWire/PulseAudio no está disponible.") from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioInputNoDisponible("'pactl get-default-source' tardó demasiado.") from exc
        fuente_default = result.stdout.strip()

    if not fuente_default or _normalizar_nombre(objetivo) not in _normalizar_nombre(fuente_default):
        raise AudioInputNoDisponible(
            f"La fuente PipeWire/PulseAudio por defecto no coincide con {objetivo!r}: "
            f"{fuente_default or '(sin fuente)'}."
        )
    return fuente_default


def resolver_dispositivo_alsa(match: str | None = None, listado: str | None = None) -> str:
    """Devuelve ``plughw:<card>,<device>`` para la placa ALSA configurada.

    ``listado`` existe para pruebas; en producción se obtiene con ``arecord
    -l``. No hay fallback al dispositivo por defecto: grabar el micrófono
    incorrecto es peor que informar un error accionable.
    """
    objetivo = (match or obtener_audio_input_match()).casefold()
    if listado is None:
        try:
            result = subprocess.run(
                ["arecord", "-l"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise AudioInputNoDisponible("No se encontró 'arecord' (instalá alsa-utils).") from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioInputNoDisponible("'arecord -l' tardó demasiado al listar dispositivos.") from exc
        listado = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")

    patron = re.compile(r"^card\s+(\d+):.*?device\s+(\d+):.*$", re.IGNORECASE)
    candidatos: list[str] = []
    for linea in listado.splitlines():
        normalizada = linea.strip()
        if not normalizada:
            continue
        encontrado = patron.match(normalizada)
        if not encontrado:
            continue
        candidatos.append(normalizada)
        if objetivo in normalizada.casefold():
            return f"plughw:{encontrado.group(1)},{encontrado.group(2)}"

    detalle = "; ".join(candidatos) or "ninguna entrada capturable"
    raise AudioInputNoDisponible(
        f"No se encontró una entrada ALSA que coincida con {match or obtener_audio_input_match()!r}. "
        f"Detectadas: {detalle}"
    )


def resolver_indice_pyaudio(pa: Any, match: str | None = None) -> int:
    """Encuentra el índice de entrada PyAudio cuyo nombre contiene ``match``."""
    objetivo = (match or obtener_audio_input_match()).casefold()
    candidatos: list[str] = []
    for index in range(int(pa.get_device_count())):
        info = pa.get_device_info_by_index(index)
        if int(info.get("maxInputChannels", 0) or 0) <= 0:
            continue
        nombre = str(info.get("name", ""))
        candidatos.append(f"{index}: {nombre}")
        if objetivo in nombre.casefold():
            return index

    detalle = "; ".join(candidatos) or "ninguna entrada capturable"
    raise AudioInputNoDisponible(
        f"No se encontró una entrada PyAudio que coincida con {match or obtener_audio_input_match()!r}. "
        f"Detectadas: {detalle}"
    )
