#!/usr/bin/env python3
"""
Tests Task 2: Factory central crear_estado_inicial.

Ejecutar: python tests/test_estado.py
No requiere Ollama.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent.graph_state import crear_estado_inicial, _claves_estado


def test_estado_tiene_todas_las_claves():
    estado = crear_estado_inicial("hola", {}, True)
    esperadas = _claves_estado()
    faltan = esperadas - set(estado.keys())
    assert not faltan, f"faltan claves en el estado: {faltan}"


def test_estado_incluye_campos_plan():
    estado = crear_estado_inicial("hola", {}, True)
    assert estado["plan_activo"] is False
    assert estado["plan_pasos"] == []
    assert estado["plan_index"] == 0
    assert estado["plan_resultados"] == []


def test_estado_normaliza_mem():
    estado = crear_estado_inicial("hola", {}, True)
    mem = estado["mem"]
    assert "preferencias" in mem
    assert "flatpaks" in mem
    assert "historial_comandos" in mem
    assert "conversacion" in mem


def test_estado_preserva_orden_y_modo():
    estado = crear_estado_inicial("abre firefox", {}, False)
    assert estado["orden"] == "abre firefox"
    assert estado["modo_autonomo"] is False


def test_estado_mem_none_no_crashea():
    estado = crear_estado_inicial("hola", None, True)
    assert isinstance(estado["mem"], dict)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fallos = 0
    for fn in fns:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except Exception as e:
            fallos += 1
            print(f"❌ {fn.__name__}: {e}")
    print(f"\n{len(fns) - fallos}/{len(fns)} tests OK")
    sys.exit(1 if fallos else 0)
