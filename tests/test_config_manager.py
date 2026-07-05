#!/usr/bin/env python3
"""
test_config_manager.py — Tests de la nueva arquitectura de infraestructura.

Usa archivos temporales para no tocar core/config/config.json.
"""

import sys
import tempfile
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_config_manager():
    print("🧪 Testeando ConfigManager...")

    from core.config.config_manager import ConfigManager

    # Usar archivo temporal, no el config.json de producción
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump({}, f)
        tmp_path = Path(f.name)

    try:
        config = ConfigManager(config_path=tmp_path)

        # Defaults
        assert config.get("MODELO") == "ornith:9b", f"MODELO incorrecto: {config.get('MODELO')}"
        print("✓ MODELO por defecto: ornith:9b")

        # Escritura
        assert config.set("TEMPERATURE", 0.8), "No se pudo escribir TEMPERATURE"
        assert config.get("TEMPERATURE") == 0.8
        print("✓ Escritura y lectura de TEMPERATURE: 0.8")

        # Validación rechaza modelo inválido
        assert not config.set("MODELO", "modelo_invalido:9b", validate=True)
        print("✓ Validación rechaza MODELO inválido correctamente")

        # Propiedades de conveniencia
        config.modelo = "ornith:9b"
        assert config.get("MODELO") == "ornith:9b"
        print("✓ Propiedades de conveniencia funcionan")

        # Bool
        config.set("DEBUG", True)
        assert config.get("DEBUG") is True
        print("✓ Persistencia de booleanos funciona")

        # Reset a default
        config.reset("TEMPERATURE")
        assert config.get("TEMPERATURE") == 0.6
        print("✓ Reset a default funciona")

    finally:
        tmp_path.unlink(missing_ok=True)

    print("✅ Todos los tests de ConfigManager pasaron!\n")


def test_event_bus():
    print("🧪 Testeando EventBus...")

    from core.events import EventBus, Event

    # Instancia nueva para no contaminar el singleton global
    bus = EventBus()

    received = []

    def handler(e: Event):
        received.append(e)

    bus.subscribe(handler, type="test_event")
    bus.publish(Event("test_event", {"data": "hola"}))

    assert len(received) == 1
    assert received[0].payload["data"] == "hola"
    print("✓ Suscripción y publicación funcionan")

    bus.unsubscribe(handler)
    bus.publish(Event("test_event", {"data": "post-unsub"}))
    assert len(received) == 1
    print("✓ Desuscripción funciona")

    # Historial
    hist = bus.get_history(type="test_event")
    assert len(hist) == 2  # ambos eventos quedaron en historial
    print("✓ Historial de eventos funciona")

    print("✅ Todos los tests de EventBus pasaron!\n")


def test_state_manager():
    print("🧪 Testeando StateManager...")

    from core.state_manager import StateManager

    state = StateManager()

    state.set("orden", "prueba")
    state.set("modo_autonomo", True)
    assert state.get("orden") == "prueba"
    assert state.get("modo_autonomo") is True
    print("✓ Escritura y lectura funcionan")

    snap = state.snapshot()
    assert snap is not None
    print("✓ Snapshot creado")

    state.set("orden", "modificada")
    assert state.get("orden") == "modificada"

    state.restore(snap)
    assert state.get("orden") == "prueba"
    print("✓ Restore a snapshot funciona")

    state.set_multiple({"a": 1, "b": 2})
    assert state.get("a") == 1 and state.get("b") == 2
    print("✓ set_multiple funciona")

    print("✅ Todos los tests de StateManager pasaron!\n")


def test_tui_imports():
    print("🧪 Verificando imports de la TUI...")

    from tui.app import run
    print("✓ tui.app importa correctamente")

    from tui.widgets import PlanPanel
    print("✓ tui.widgets.PlanPanel importa correctamente")

    from tui.widgets.config import ConfigPanel
    print("✓ tui.widgets.config.ConfigPanel importa correctamente")

    print("✅ Todos los imports de la TUI son correctos!\n")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 TESTEO DE NUEVA ARQUITECTURA TUI")
    print("=" * 60 + "\n")

    try:
        test_config_manager()
        test_event_bus()
        test_state_manager()
        test_tui_imports()

        print("=" * 60)
        print("✅ TODOS LOS TESTS PASARON")
        print("=" * 60)
        print("\nArquitectura lista:")
        print("  · ConfigManager  — hot reload, validación, persistencia")
        print("  · EventBus       — pub/sub desacoplado")
        print("  · StateManager   — estado único con snapshots")
        print("  · TUI Textual    — panel de configuración interactivo")
        print("\nEjecución: python run.py")

    except AssertionError as e:
        print(f"\n❌ Test falló: {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n❌ Error inesperado: {e}")
        traceback.print_exc()
        sys.exit(1)
