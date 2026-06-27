#!/usr/bin/env python3
"""
Tests Fix 5: node_memory con fallback a charla (text) cuando NO matchea patrón.

En el log original, "te quedaste con eso en cache?..." se ruteó a memory y
node_memory devolvía el genérico vacío "Operación de memoria completada.".
Ahora, si ningún patrón de memoria matchea, delega en node_text (charla).

No requiere Ollama: se mockean _llm_chat / construir_contexto_memoria / guardar_memoria.

Ejecutar: python tests/test_memory_fallback.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
import core.memory.memory_manager as mm
from core.agent.graph_nodes import node_memory
from core.agent.graph_state import crear_estado_inicial


def _estado(orden, mem=None):
    return crear_estado_inicial(orden, mem or {}, True)


# ── Patrones de memoria REALES → NO hacen fallback ────────────────────

def test_mostrar_memoria_vuelca_json():
    out = node_memory(_estado("muéstrame tu memoria"))
    assert "preferencias" in out["final_response"]
    assert out["final_response"] != "Perdón, no te seguí bien. ¿Me lo repetís?"


def test_borra_conversacion():
    out = node_memory(_estado("borra la conversación"))
    assert out["final_response"] == "Historial borrado."


def test_registra_nombre():
    orig = mm.guardar_memoria
    mm.guardar_memoria = lambda *a, **k: None
    try:
        out = node_memory(_estado("mi nombre es Thomas"))
    finally:
        mm.guardar_memoria = orig
    assert "Thomas" in out["final_response"]


# ── Sin patrón → fallback a charla (node_text) ────────────────────────

def _run_sin_patron(orden, respuesta_chat="Perdón, ¿qué necesitás?"):
    capturado = {}

    def fake_llm(system, user, on_token=None, stop=None, stop_regex=None):
        capturado["system"] = system
        return respuesta_chat

    orig_llm = gn._llm_chat
    orig_ctx = gn.construir_contexto_memoria
    gn._llm_chat = fake_llm
    gn.construir_contexto_memoria = lambda mem: "CTX"
    try:
        out = node_memory(_estado(orden))
    finally:
        gn._llm_chat = orig_llm
        gn.construir_contexto_memoria = orig_ctx
    return out, capturado


def test_sin_patron_cae_a_text():
    out, capturado = _run_sin_patron(
        "te quedaste con eso en cache? no te ordené eso",
        respuesta_chat="Perdón, tenés razón, no quise hacer eso.",
    )
    assert out["final_response"] == "Perdón, tenés razón, no quise hacer eso."
    # NO debe ser el genérico vacío de memoria
    assert out["final_response"] != "Operación de memoria completada."
    # Delegó a node_text → persona de chat (sin protocolo [SHELL])
    assert "[SHELL]" not in capturado.get("system", "")


def test_sin_patron_no_devuelve_generico_memoria():
    out, _ = _run_sin_patron("¿vos te acordás de lo que hablamos?")
    assert out["final_response"] != "Operación de memoria completada."


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
