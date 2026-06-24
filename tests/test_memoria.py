#!/usr/bin/env python3
"""
Tests Task 1: Esquema obligatorio de memoria + acceso defensivo.

Ejecutar: python tests/test_memoria.py
No requiere Ollama (no llama al LLM).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory.memory_manager import normalizar_mem
from core.memory.context_builder import construir_contexto_memoria

_CLAVES = {"preferencias", "flatpaks", "historial_comandos", "conversacion"}
_CLAVES_PREFS = {"nombre_usuario", "navegador", "notas"}


def _assert_esquema(mem):
    assert _CLAVES.issubset(mem.keys()), f"faltan claves: {_CLAVES - set(mem.keys())}"
    assert _CLAVES_PREFS.issubset(mem["preferencias"].keys())
    assert isinstance(mem["preferencias"]["notas"], list)
    assert isinstance(mem["flatpaks"], dict)
    assert isinstance(mem["historial_comandos"], list)
    assert isinstance(mem["conversacion"], list)


def test_normalizar_vacio():
    _assert_esquema(normalizar_mem({}))


def test_normalizar_none():
    _assert_esquema(normalizar_mem(None))


def test_normalizar_parcial_prefs_vacias():
    mem = normalizar_mem({"preferencias": {}})
    _assert_esquema(mem)
    assert mem["preferencias"]["nombre_usuario"] == "Thomas"


def test_normalizar_preserva_valores_existentes():
    mem = normalizar_mem({"preferencias": {"nombre_usuario": "Ana"}, "flatpaks": {"firefox": "org.mozilla.firefox"}})
    assert mem["preferencias"]["nombre_usuario"] == "Ana"
    assert mem["flatpaks"]["firefox"] == "org.mozilla.firefox"
    _assert_esquema(mem)


def test_normalizar_tipos_corruptos():
    # tipos incorrectos deben ser reemplazados por defaults
    mem = normalizar_mem({"flatpaks": "no-es-dict", "conversacion": 42, "preferencias": {"notas": "no-es-lista"}})
    _assert_esquema(mem)


def test_normalizar_completo():
    completo = {
        "preferencias": {"nombre_usuario": "Thomas", "navegador": "brave", "notas": ["x"]},
        "flatpaks": {},
        "historial_comandos": [],
        "conversacion": [],
    }
    _assert_esquema(normalizar_mem(completo))


def test_context_builder_mem_vacio():
    # No debe lanzar KeyError con mem vacío normalizado
    texto = construir_contexto_memoria(normalizar_mem({}))
    assert isinstance(texto, str)
    assert "PERFIL DEL CREADOR" in texto


def test_context_builder_mem_crudo_vacio():
    # Incluso sin normalizar, ya no debe crashear (acceso .get defensivo)
    texto = construir_contexto_memoria({})
    assert isinstance(texto, str)


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
