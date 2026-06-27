#!/usr/bin/env python3
"""
Test Fix 8: _llm_chat pasa keep_alive y opciones de rendimiento a ollama.chat.

No requiere Ollama: se mockea gn.ollama.chat para capturar los kwargs.

Ejecutar: python tests/test_llm_options.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.config.settings import NUM_CTX, OLLAMA_KEEP_ALIVE, OLLAMA_GEN_OPTIONS


def _capturar_chat():
    capturado = {}

    def fake_chat(**kwargs):
        capturado.update(kwargs)
        yield {"message": {"content": "ok"}}

    original = gn.ollama.chat
    gn.ollama.chat = fake_chat
    return capturado, original


def test_keep_alive_y_opciones_se_pasan():
    capturado, original = _capturar_chat()
    try:
        resp = gn._llm_chat(system="s", user="u")
    finally:
        gn.ollama.chat = original

    assert resp == "ok"
    # keep_alive del settings
    assert capturado.get("keep_alive") == OLLAMA_KEEP_ALIVE, capturado.get("keep_alive")
    # options: num_ctx + knobs de OLLAMA_GEN_OPTIONS (p.ej. num_batch)
    opts = capturado.get("options", {})
    assert opts.get("num_ctx") == NUM_CTX, opts
    for k, v in OLLAMA_GEN_OPTIONS.items():
        assert opts.get(k) == v, (k, opts.get(k), v)
    # streaming activado
    assert capturado.get("stream") is True


def test_no_muta_settings_options():
    # _llm_chat debe COPIAR OLLAMA_GEN_OPTIONS (no mutar el dict global).
    antes = dict(OLLAMA_GEN_OPTIONS)
    capturado, original = _capturar_chat()
    try:
        gn._llm_chat(system="s", user="u")
    finally:
        gn.ollama.chat = original
    assert dict(OLLAMA_GEN_OPTIONS) == antes, "OLLAMA_GEN_OPTIONS no debe mutarse"
    assert "num_ctx" not in OLLAMA_GEN_OPTIONS, "num_ctx no debe filtrarse al dict global"


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
