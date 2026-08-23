#!/usr/bin/env python3
"""Pruebas del scheduler no bloqueante de consolidación."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.memory.consolidator as consolidator


def _esperar_scheduler(timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with consolidator._scheduler_lock:
            if not consolidator._scheduler_running:
                return
        time.sleep(0.01)
    raise AssertionError("el scheduler de consolidación no terminó")


def _reset_scheduler() -> None:
    _esperar_scheduler()
    with consolidator._scheduler_lock:
        consolidator._scheduler_requested_again = False
        consolidator._memories_to_refresh.clear()


def test_umbral_no_llama_al_llm_con_menos_de_12_turnos():
    original = consolidator.obtener_turnos_pendientes_de_resumen
    consolidator.obtener_turnos_pendientes_de_resumen = lambda: [{"id": n} for n in range(11)]
    try:
        assert consolidator.consolidar_resumen() is None
    finally:
        consolidator.obtener_turnos_pendientes_de_resumen = original


def test_scheduler_coalesce_y_refresca_todas_las_memorias():
    _reset_scheduler()
    original = consolidator.consolidar_resumen
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_consolidar():
        calls.append(True)
        if len(calls) == 1:
            started.set()
            assert release.wait(1.0)
            return "resumen actualizado"
        return None

    consolidator.consolidar_resumen = fake_consolidar
    mem_tui = {"resumen": "viejo"}
    mem_api = {"resumen": "viejo"}
    try:
        assert consolidator.programar_consolidacion(mem_tui) is True
        assert started.wait(1.0)
        assert consolidator.programar_consolidacion(mem_api) is False
        release.set()
        _esperar_scheduler()
    finally:
        consolidator.consolidar_resumen = original
        _reset_scheduler()

    assert len(calls) == 2  # la solicitud concurrente quedó coalescida
    assert mem_tui["resumen"] == "resumen actualizado"
    assert mem_api["resumen"] == "resumen actualizado"


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
