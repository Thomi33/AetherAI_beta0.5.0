#!/usr/bin/env python3
"""
Tests del FIX anti-alucinación de node_codigo (stop_regex en el primer bloque).

node_codigo puede generar código bash/sh y ejecutarlo con `bash archivo` →
afecta a la ruta de SHELL. Igual que node_shell, el LLM podía alucinar
múltiples bloques ```...``` con salidas inventadas y colgarse. FIX: _llm_chat
acepta stop_regex y corta al completar el PRIMER bloque ```...``` (un substring
no sirve porque ``` abre y cierra).

NO requiere Ollama: se mockean _llm_chat / ollama.chat / ejecutar_comando.

Ejecutar: python tests/test_codigo_stop.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import _llm_chat, node_codigo, _PATRON_BLOQUE_CODIGO


class _patch:
    def __init__(self, obj, attr, valor):
        self.obj, self.attr, self.valor = obj, attr, valor
    def __enter__(self):
        self.orig = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.valor)
        return self
    def __exit__(self, *a):
        setattr(self.obj, self.attr, self.orig)


def _fake_ollama_stream(texto):
    """Devuelve un fake de ollama.chat que streamea `texto` char por char."""
    def fake_chat(model, messages, stream, options, **kwargs):
        for ch in texto:
            yield {"message": {"content": ch}}
    return fake_chat


_DOS_BLOQUES = (
    "Aquí está el script:\n"
    "```python\nprint('uno')\n```\n"
    "Salida simulada: uno\n"
    "```python\nprint('dos')\n```\n"
)


# ──────────────────────────────────────────────────────────────────────
# _llm_chat: el corte por stop_regex detiene tras el primer bloque
# ──────────────────────────────────────────────────────────────────────

def test_stop_regex_corta_en_primer_bloque():
    with _patch(gn.ollama, "chat", _fake_ollama_stream(_DOS_BLOQUES)):
        out = _llm_chat("sys", "user", stop_regex=_PATRON_BLOQUE_CODIGO)
    assert "print('uno')" in out
    assert "print('dos')" not in out, "no debe llegar al segundo bloque"


def test_sin_stop_regex_no_corta():
    with _patch(gn.ollama, "chat", _fake_ollama_stream(_DOS_BLOQUES)):
        out = _llm_chat("sys", "user")  # default → sin corte
    assert "print('uno')" in out and "print('dos')" in out


# ──────────────────────────────────────────────────────────────────────
# node_codigo: pasa stop_regex y ejecuta SOLO el primer bloque
# ──────────────────────────────────────────────────────────────────────

class _exec_env:
    def __init__(self):
        self.ejecutados = []
        self._orig = {}
    def __enter__(self):
        self._orig["ejecutar_comando"] = gn.ejecutar_comando
        self._orig["registrar_comando"] = gn.registrar_comando
        gn.ejecutar_comando = lambda cmd: (self.ejecutados.append(cmd) or ("ok", False))
        gn.registrar_comando = lambda *a, **k: None
        return self
    def __exit__(self, *a):
        gn.ejecutar_comando = self._orig["ejecutar_comando"]
        gn.registrar_comando = self._orig["registrar_comando"]


def test_node_codigo_pasa_stop_regex():
    capturado = {}
    def fake_llm(system, user, on_token=None, stop=None, stop_regex=None):
        capturado["stop_regex"] = stop_regex
        return "```python\nprint('uno')\n```"
    with _patch(gn, "_llm_chat", fake_llm), _exec_env():
        node_codigo({"orden": "escribe un script", "mem": {}, "modo_autonomo": True})
    assert capturado["stop_regex"] == _PATRON_BLOQUE_CODIGO
    if os.path.exists("/tmp/aether_code.py"):
        os.remove("/tmp/aether_code.py")


def test_node_codigo_ejecuta_solo_primer_bloque():
    with _patch(gn, "_llm_chat", lambda *a, **k: _DOS_BLOQUES), _exec_env() as env:
        node_codigo({"orden": "escribe un script python", "mem": {}, "modo_autonomo": True})
    assert env.ejecutados == ["python3 /tmp/aether_code.py"], env.ejecutados
    with open("/tmp/aether_code.py") as f:
        contenido = f.read()
    assert "print('uno')" in contenido
    assert "print('dos')" not in contenido
    os.remove("/tmp/aether_code.py")


def test_node_codigo_bash_usa_ruta_shell():
    bloque_bash = "```bash\necho hola\n```"
    with _patch(gn, "_llm_chat", lambda *a, **k: bloque_bash), _exec_env() as env:
        node_codigo({"orden": "escribe un script bash", "mem": {}, "modo_autonomo": True})
    # bash → extensión .sh → se ejecuta con `bash` (ruta de shell)
    assert env.ejecutados == ["bash /tmp/aether_code.sh"], env.ejecutados
    os.remove("/tmp/aether_code.sh")


def test_node_codigo_fallback_shell_si_usa_SHELL():
    # El modelo usó [SHELL] tee (crear archivo) en vez de bloque ``` →
    # node_codigo debe EJECUTAR ese comando (antes quedaba sin ejecutar).
    resp = "Aquí está:\n[SHELL] tee ~/fecha.sh << 'EOF'\n#!/bin/bash\ndate\nEOF [/SHELL]\nlisto"
    with _patch(gn, "_llm_chat", lambda *a, **k: resp), _exec_env() as env:
        out = node_codigo({"orden": "escribí un script bash", "mem": {}, "modo_autonomo": True})
    assert len(env.ejecutados) == 1, env.ejecutados
    assert env.ejecutados[0].startswith("tee ~/fecha.sh"), env.ejecutados
    assert out["shell_command"].startswith("tee ~/fecha.sh")


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
