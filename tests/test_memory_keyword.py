#!/usr/bin/env python3
"""
Tests Fix 6: el keyword ultra-amplio "años" de _KW_MEMORY se reemplaza por un
regex específico de EDAD ("tengo N años"). Así "hace años que..." ya NO se
rutea a memory, pero "tengo 30 años" sí.

No requiere Ollama. Ejecutar: python tests/test_memory_keyword.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.memory.memory_manager as mm
from core.agent.graph_nodes import _detectar_intent_keywords, node_memory
from core.agent.graph_state import crear_estado_inicial


# ── Routing por keyword ────────────────────────────────────────────────

def test_tengo_n_anios_rutea_a_memory():
    assert _detectar_intent_keywords("tengo 30 años") == "memory"
    assert _detectar_intent_keywords("ya tengo 41 años, ¿sabías?") == "memory"
    # sin acento (como queda normalizado) también
    assert _detectar_intent_keywords("tengo 25 anos") == "memory"


def test_anios_suelto_ya_no_rutea_a_memory():
    # Frases con "años" que NO son registro de edad → NO deben ir a memory.
    for orden in (
        "hace años que no toco esto",
        "el proyecto lleva años en desarrollo",
        "pasaron tres años desde eso",
    ):
        assert _detectar_intent_keywords(orden) != "memory", orden


def test_otros_patrones_memory_intactos():
    assert _detectar_intent_keywords("muéstrame tu memoria") == "memory"
    assert _detectar_intent_keywords("mi nombre es Thomas") == "memory"
    assert _detectar_intent_keywords("recuerda que me gusta el mate") == "memory"


# ── node_memory registra edad (con y sin acento) ──────────────────────

def _registrar(orden):
    orig = mm.guardar_memoria
    capturado = {}
    mm.guardar_memoria = lambda mem: capturado.update(mem.get("preferencias", {}))
    try:
        out = node_memory(crear_estado_inicial(orden, {}, True))
    finally:
        mm.guardar_memoria = orig
    return out, capturado


def test_node_memory_registra_edad_con_acento():
    out, prefs = _registrar("tengo 30 años")
    assert "30" in out["final_response"]
    assert prefs.get("edad") == 30


def test_node_memory_registra_edad_sin_acento():
    out, prefs = _registrar("tengo 28 anos")
    assert prefs.get("edad") == 28


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
