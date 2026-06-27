#!/usr/bin/env python3
"""
Tests Fix 3: persona conversacional separada del backstory de ejecución.

node_text (charla) debe usar construir_persona_chat (SIN protocolo [SHELL] ni
formato de ejecución), mientras shell/codigo/etc. siguen usando
construir_backstory (CON [SHELL]).

No requiere Ollama: se mockean _llm_chat y construir_contexto_memoria (DB).

Ejecutar: python tests/test_chat_persona.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import node_text, _system_prompt, _system_prompt_chat
from core.agent.prompts import construir_persona_chat, construir_backstory
from core.agent.graph_state import crear_estado_inicial


_PROHIBIDOS_EN_CHAT = ("[SHELL]", "[/SHELL]", "Thought:", "Action:", "pacman -S")


def test_persona_chat_sin_protocolo_ejecucion():
    p = construir_persona_chat("CTX")
    for x in _PROHIBIDOS_EN_CHAT:
        assert x not in p, f"la persona de chat NO debe contener: {x}"


def test_persona_chat_incluye_contexto_e_identidad():
    p = construir_persona_chat("MARCADOR_CTX_123")
    assert "MARCADOR_CTX_123" in p, "debe inyectar el contexto de memoria"
    assert "Aether" in p


def test_backstory_ejecucion_conserva_protocolo_shell():
    # El prompt de EJECUCIÓN (shell/codigo/launch) NO se tocó.
    b = construir_backstory("CTX")
    assert "[SHELL]" in b


def _patch_ctx(valor="CTX_MARCADOR"):
    orig = gn.construir_contexto_memoria
    gn.construir_contexto_memoria = lambda mem: valor
    return orig


def test_system_prompt_chat_vs_ejecucion_divergen():
    orig = _patch_ctx()
    try:
        mem = crear_estado_inicial("x", {}, True)["mem"]
        chat = _system_prompt_chat(mem)
        ejec = _system_prompt(mem)
    finally:
        gn.construir_contexto_memoria = orig
    assert "[SHELL]" not in chat, "el system de chat no debe tener protocolo [SHELL]"
    assert "[SHELL]" in ejec, "el system de ejecución debe conservar [SHELL]"
    assert "CTX_MARCADOR" in chat and "CTX_MARCADOR" in ejec


def test_node_text_usa_persona_chat():
    capturado = {}

    def fake_llm(system, user, on_token=None, stop=None, stop_regex=None):
        capturado["system"] = system
        return "¡hola! todo bien por acá"

    orig_llm = gn._llm_chat
    orig_ctx = _patch_ctx()
    gn._llm_chat = fake_llm
    try:
        estado = crear_estado_inicial("hola", {}, True)
        out = node_text(estado)
    finally:
        gn._llm_chat = orig_llm
        gn.construir_contexto_memoria = orig_ctx

    assert "[SHELL]" not in capturado["system"], "node_text debe usar la persona de chat (sin [SHELL])"
    assert out["final_response"] == "¡hola! todo bien por acá"


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
