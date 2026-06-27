#!/usr/bin/env python3
"""
Tests Fix 4: endurecer node_text contra [SHELL]/ReAct en charla.

Aunque la persona de chat (Fix 3) no empuja a ejecutar, si el modelo IGUAL
emite un bloque [SHELL] o formato ReAct, node_text debe:
  - cortar la generación (stop=["[SHELL]"]),
  - limpiar la respuesta (quedarse con la prosa previa),
  - usar un fallback conversacional si no queda prosa.

No requiere Ollama: se mockean _llm_chat y construir_contexto_memoria (DB).

Ejecutar: python tests/test_chat_cleanup.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import node_text, _limpiar_respuesta_chat
from core.agent.graph_state import crear_estado_inicial


# ── 1. Helper de limpieza ─────────────────────────────────────────────

def test_limpia_prosa_normal_intacta():
    assert _limpiar_respuesta_chat("Hola, todo bien por acá") == "Hola, todo bien por acá"


def test_limpia_corta_en_shell():
    assert _limpiar_respuesta_chat("Para diagnosticar [SHELL] lshw [/SHELL]") == "Para diagnosticar"


def test_limpia_shell_puro_queda_vacio():
    assert _limpiar_respuesta_chat("[SHELL] lshw -C processor [/SHELL]") == ""


def test_limpia_shell_sin_cierre():
    # stop=["[SHELL]"] suele cortar antes del cierre → bloque sin [/SHELL]
    assert _limpiar_respuesta_chat("Te fijo eso [SHELL] free -h") == "Te fijo eso"


def test_limpia_react_markers():
    assert _limpiar_respuesta_chat("Thought: voy a saludar\nHola!") == "Hola!"


def test_limpia_preserva_bloques_de_codigo():
    # En charla, un ejemplo de código en ``` es legítimo y NO se toca.
    txt = "Mirá este ejemplo:\n```python\nprint('hola')\n```"
    assert _limpiar_respuesta_chat(txt) == txt


# ── 2. Integración en node_text ───────────────────────────────────────

def _patch(system_ctx="CTX", retorno=""):
    """Mockea construir_contexto_memoria (DB) y _llm_chat (retorno fijo)."""
    capturado = {}

    def fake_llm(system, user, on_token=None, stop=None, stop_regex=None):
        capturado["stop"] = stop
        return retorno

    orig_ctx = gn.construir_contexto_memoria
    orig_llm = gn._llm_chat
    gn.construir_contexto_memoria = lambda mem: system_ctx
    gn._llm_chat = fake_llm
    return capturado, (orig_ctx, orig_llm)


def _restore(orig):
    gn.construir_contexto_memoria, gn._llm_chat = orig


def _run_node_text(retorno):
    capturado, orig = _patch(retorno=retorno)
    try:
        out = node_text(crear_estado_inicial("hola", {}, True))
    finally:
        _restore(orig)
    return out, capturado


def test_node_text_pasa_stop_shell():
    out, capturado = _run_node_text("¡Buenas! ¿Cómo va?")
    assert capturado["stop"] == ["[SHELL]"], "node_text debe cortar en [SHELL]"
    assert out["final_response"] == "¡Buenas! ¿Cómo va?"


def test_node_text_limpia_shell_colado():
    out, _ = _run_node_text("Claro, te ayudo [SHELL] lshw [/SHELL]")
    assert out["final_response"] == "Claro, te ayudo"
    assert "[SHELL]" not in out["final_response"]


def test_node_text_fallback_si_solo_comando():
    out, _ = _run_node_text("[SHELL] lshw -C processor [/SHELL]")
    assert out["final_response"] == "Perdón, no te seguí bien. ¿Me lo repetís?"


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
