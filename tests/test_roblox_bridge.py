from __future__ import annotations

from pathlib import Path

from core.tools.roblox_bridge import RobloxRuntime


def test_roblox_runtime_focuses_sober_before_start(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(
        "core.tools.roblox_bridge.computer_control.buscar_ventana",
        lambda name: calls.append(("find", name)) or ("0x1", None),
    )
    monkeypatch.setattr(
        "core.tools.roblox_bridge.computer_control.enfocar_ventana",
        lambda window: calls.append(("focus", window)) or ("OK", False),
    )

    class FakeProcess:
        pid = 123

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            calls.append(("wait", timeout))

    monkeypatch.setattr("core.tools.roblox_bridge.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    runtime = RobloxRuntime(str(tmp_path))

    ok, message = runtime.start()

    assert ok is True
    assert "iniciado" in message
    assert calls[:2] == [("find", "org.vinegarhq.Sober"), ("focus", "0x1")]
    assert runtime.running is True


def test_roblox_runtime_rejects_missing_runtime(monkeypatch, tmp_path):
    runtime = RobloxRuntime(str(Path(tmp_path) / "missing"))
    ok, message = runtime.start()
    assert ok is False
    assert "No existe" in message
