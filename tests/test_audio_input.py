#!/usr/bin/env python3
"""Pruebas de selección explícita de AudioBox y del protocolo STT."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.services.audio_input import (
    AudioInputNoDisponible,
    resolver_dispositivo_alsa,
    resolver_fuente_pulse,
    resolver_indice_pyaudio,
)
import core.services.stt_service as stt


ALSA_LIST = """card 1: PCH [HDA Intel PCH], device 0: ALC [ALC]
card 3: AudioBox [AudioBox USB 96], device 0: USB Audio [USB Audio]
"""


class _FakePyAudio:
    def __init__(self):
        self._devices = [
            {"name": "Monitor of Built-in", "maxInputChannels": 2},
            {"name": "AudioBox USB 96: USB Audio (hw:3,0)", "maxInputChannels": 2},
        ]

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, index):
        return self._devices[index]


class _FakeRecorderProc:
    def poll(self):
        return None


class _FakeStdin:
    def __init__(self):
        self.written = ""

    def write(self, value):
        self.written += value

    def flush(self):
        pass


class _FakeStdout:
    def __init__(self, line):
        self._line = line

    def readline(self):
        line, self._line = self._line, ""
        return line


class _FakeWorkerProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout('{"id": 1, "ok": true, "text": "Aether"}\n')
        self.stderr = None

    def poll(self):
        return None


def test_resuelve_alsa_y_pyaudio_por_nombre():
    assert resolver_dispositivo_alsa("AudioBox USB 96", ALSA_LIST) == "plughw:3,0"
    assert resolver_fuente_pulse(
        "AudioBox USB 96", "alsa_input.usb-PreSonus_AudioBox_USB_96_000000-00.pro-input-0"
    ).startswith("alsa_input.usb-PreSonus")
    assert resolver_indice_pyaudio(_FakePyAudio(), "AudioBox USB 96") == 1


def test_no_hay_fallback_a_dispositivo_default():
    try:
        resolver_dispositivo_alsa("AudioBox USB 96", "card 0: PCH [Built-in], device 0: ALC [ALC]")
    except AudioInputNoDisponible as exc:
        assert "AudioBox USB 96" in str(exc)
    else:
        raise AssertionError("debía rechazar una placa distinta")


def test_grabador_pasa_dispositivo_explicito_a_arecord():
    original_resolver = stt.resolver_fuente_pulse
    original_popen = stt.subprocess.Popen
    recorded = {}
    stt.resolver_fuente_pulse = lambda: "alsa_input.usb-PreSonus_AudioBox_USB_96_000000-00.pro-input-0"

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        recorded.update(kwargs)
        return _FakeRecorderProc()

    stt.subprocess.Popen = fake_popen
    try:
        grabador = stt.GrabadorAudio()
        grabador.iniciar()
    finally:
        stt.resolver_fuente_pulse = original_resolver
        stt.subprocess.Popen = original_popen

    command = recorded["command"]
    assert command[command.index("-D") + 1] == "pulse"
    assert recorded.get("env", {}).get("PULSE_SOURCE", "").startswith("alsa_input.usb-PreSonus")


def test_worker_envia_initial_prompt():
    worker = stt.SttWorker()
    worker._proc = _FakeWorkerProc()
    worker._listo = True
    result = worker.transcribir("/tmp/audio.wav", idioma="es", initial_prompt="Aether, SQLite")
    payload = json.loads(worker._proc.stdin.written)
    assert payload["initial_prompt"] == "Aether, SQLite"
    assert result["text"] == "Aether"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"✅ {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"❌ {test.__name__}: {exc}")
    sys.exit(bool(failures))
