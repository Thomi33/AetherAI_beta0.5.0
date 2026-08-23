#!/usr/bin/env python3
"""Prueba aislada del enlace wake word → handler de focus."""

import importlib.util
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
DAEMON_PATH = Path.home() / "GamesNvme/aether_wakeword_training/wakeword_daemon.py"


def _cargar_daemon_con_dependencias_falsas():
    replacements = {
        "pyaudio": types.SimpleNamespace(paInt16=8),
        "openwakeword": types.ModuleType("openwakeword"),
        "openwakeword.model": types.SimpleNamespace(Model=object),
    }
    originals = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    try:
        spec = importlib.util.spec_from_file_location("wakeword_daemon_test", DAEMON_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def test_deteccion_lanza_handler_y_registra_salida():
    daemon = _cargar_daemon_con_dependencias_falsas()
    original_popen = daemon.subprocess.Popen
    with tempfile.TemporaryDirectory() as directory:
        daemon.FOCUS_LOG_PATH = Path(directory) / "focus.log"
        calls = []

        class _FakeProcess:
            pid = 4242

        def fake_popen(command, **kwargs):
            calls.append((command, kwargs))
            return _FakeProcess()

        daemon.subprocess.Popen = fake_popen
        try:
            assert daemon.lanzar_focus_handler() == 4242
        finally:
            daemon.subprocess.Popen = original_popen

    command, kwargs = calls[0]
    assert command[-1].endswith("scripts/aether_focus.py")
    assert kwargs["start_new_session"] is True


def test_convierte_audio_de_audiobox_48khz_a_modelo_16khz():
    daemon = _cargar_daemon_con_dependencias_falsas()
    frames = daemon.np.arange(3840, dtype=daemon.np.int16)
    audio = daemon.np.column_stack((frames, frames + 1, frames + 2, frames + 3)).reshape(-1)
    convertido = daemon.convertir_a_sample_rate_model(audio, 48000, input_channels=4)
    assert len(convertido) == 1280
    assert convertido.dtype == daemon.np.int16


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"✅ {test.__name__}")
