#!/usr/bin/env python3
"""
Tests del endurecimiento de extraer_comando_shell (anti-output-alucinado).

BUG: el LLM (en formato ReAct) metía la salida inventada de un comando dentro
de un bloque ``` SIN lenguaje, y el fallback del parser (lenguaje opcional) la
capturaba y la ejecutaba como comando → 'zsh: no matches found: Procesador(s):'.
FIX: el fallback exige tag de lenguaje shell (```bash/sh/zsh/shell).

Ejecutar: python tests/test_parser_shell.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.parser.shell_parser import extraer_comando_shell


def test_extrae_bloque_shell():
    assert extraer_comando_shell("bla [SHELL] lscpu [/SHELL] bla") == "lscpu"


def test_extrae_bloque_shell_multilinea():
    txt = "[SHELL] tee f.sh << 'EOF'\n#!/bin/bash\ndate\nEOF [/SHELL]"
    cmd = extraer_comando_shell(txt)
    assert cmd.startswith("tee f.sh") and "date" in cmd


def test_fence_con_lenguaje_bash_si_se_extrae():
    txt = "```bash\nls -la\n```"
    assert extraer_comando_shell(txt) == "ls -la"


def test_fence_SIN_lenguaje_NO_se_ejecuta():
    # Salida alucinada típica que ANTES se ejecutaba como comando
    alucinado = (
        "```\n"
        "Procesador(s):\n"
        '  Proceso de CPU: 1 "Intel(R) Core(TM) i7-8650UCPU"\n'
        "  Número de núcleo(s): 8\n"
        "```"
    )
    assert extraer_comando_shell(alucinado) is None, "no debe extraer output sin tag shell"


def test_prefiere_shell_sobre_fence():
    # Si hay [SHELL] real, se usa ese aunque haya un fence alrededor
    txt = "```\nsalida fake\n```\n[SHELL] lscpu [/SHELL]"
    assert extraer_comando_shell(txt) == "lscpu"


def test_texto_plano_sin_comando():
    assert extraer_comando_shell("solo una respuesta en prosa") is None


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
