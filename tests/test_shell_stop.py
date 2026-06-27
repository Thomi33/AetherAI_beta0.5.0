#!/usr/bin/env python3
"""
Tests del FIX anti-alucinación de node_shell (stop sequence en [/SHELL]).

BUG original: el LLM generaba múltiples bloques [SHELL]...[/SHELL] con salidas
inventadas y la generación nunca paraba → el código jamás ejecutaba el comando
real. FIX: node_shell pasa stop=["[/SHELL]"] a _llm_chat; como ollama elimina
la secuencia de stop, se re-agrega el cierre antes de extraer.

NO requiere Ollama: se mockean _llm_chat, ejecutar_comando, registrar_comando.

Ejecutar: python tests/test_shell_stop.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import node_shell, _cerrar_bloque_shell, _llm_chat


class _patch:
    def __init__(self, obj, attr, valor):
        self.obj, self.attr, self.valor = obj, attr, valor
    def __enter__(self):
        self.orig = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.valor)
        return self
    def __exit__(self, *a):
        setattr(self.obj, self.attr, self.orig)


class _shell_env:
    """Mockea ejecutar_comando + registrar_comando; registra comandos ejecutados."""
    def __init__(self, salida="salida real", error=False):
        self.salida, self.error = salida, error
        self.ejecutados = []
        self._orig = {}
    def __enter__(self):
        self._orig["ejecutar_comando"] = gn.ejecutar_comando
        self._orig["registrar_comando"] = gn.registrar_comando
        def fake_exec(cmd):
            self.ejecutados.append(cmd)
            return (self.salida, self.error)
        gn.ejecutar_comando = fake_exec
        gn.registrar_comando = lambda *a, **k: None
        return self
    def __exit__(self, *a):
        gn.ejecutar_comando = self._orig["ejecutar_comando"]
        gn.registrar_comando = self._orig["registrar_comando"]


_ESTADO = {"orden": "ejecuta un diagnóstico de mi CPU", "mem": {}, "modo_autonomo": True}


# ──────────────────────────────────────────────────────────────────────
# _cerrar_bloque_shell (repara el cierre que ollama quita con el stop)
# ──────────────────────────────────────────────────────────────────────

def test_cerrar_agrega_cierre_faltante():
    assert _cerrar_bloque_shell("[SHELL] lscpu ") == "[SHELL] lscpu [/SHELL]"


def test_cerrar_no_toca_bloque_completo():
    txt = "[SHELL] lscpu [/SHELL]"
    assert _cerrar_bloque_shell(txt) == txt


def test_cerrar_no_toca_texto_sin_shell():
    assert _cerrar_bloque_shell("solo prosa, sin comandos") == "solo prosa, sin comandos"


# ──────────────────────────────────────────────────────────────────────
# node_shell: solo ejecuta el PRIMER bloque (síntoma reproducido)
# ──────────────────────────────────────────────────────────────────────

def test_solo_ejecuta_primer_bloque():
    # El mock simula lo que en producción NO pasaría (el stop evita el 2º bloque),
    # pero confirma que aun recibiéndolos, node_shell ejecuta SOLO el primero.
    doble = (
        "[SHELL] lscpu [/SHELL]\n"
        "Salida inventada del LLM...\n"
        "[SHELL] cat /proc/cpuinfo [/SHELL]\n"
        "otra salida inventada..."
    )
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None, stop=None: doble), \
         _shell_env() as env:
        out = node_shell(dict(_ESTADO))
    assert env.ejecutados == ["lscpu"], env.ejecutados
    assert out["shell_command"] == "lscpu"


def test_node_shell_pasa_stop_correcto():
    capturado = {}
    def fake(system, user, on_token=None, stop=None):
        capturado["stop"] = stop
        return "[SHELL] lscpu [/SHELL]"
    with _patch(gn, "_llm_chat", fake), _shell_env():
        node_shell(dict(_ESTADO))
    assert capturado["stop"] == ["[/SHELL]"], capturado


def test_repara_cierre_cortado_por_stop():
    # Simula la salida REAL de producción: ollama cortó en [/SHELL] y lo quitó
    cortado = "Voy a diagnosticar tu CPU.\n[SHELL] lscpu "
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None, stop=None: cortado), \
         _shell_env() as env:
        out = node_shell(dict(_ESTADO))
    assert env.ejecutados == ["lscpu"], env.ejecutados
    assert out["shell_command"] == "lscpu"


def test_sin_comando_cae_a_retry():
    # 1ª llamada: prosa sin [SHELL]; 2ª (retry): comando válido
    respuestas = iter([
        "No entiendo bien, ¿podrías aclarar?",   # primaria → sin comando
        "[SHELL] echo hola [/SHELL]",            # retry → comando
    ])
    def fake(system, user, on_token=None, stop=None):
        return next(respuestas)
    with _patch(gn, "_llm_chat", fake), _shell_env() as env:
        out = node_shell(dict(_ESTADO))
    assert env.ejecutados == ["echo hola"], env.ejecutados
    assert out["shell_command"] == "echo hola"


def test_default_llm_chat_no_corta():
    # El default de _llm_chat (sin stop) NO debe alterar el comportamiento de
    # los nodos de prosa: verificamos que acepta el kwarg y por defecto es None.
    import inspect
    sig = inspect.signature(_llm_chat)
    assert sig.parameters["stop"].default is None


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
