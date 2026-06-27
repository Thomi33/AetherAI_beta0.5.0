#!/usr/bin/env python3
"""
Tests de AISLAMIENTO DE CONTEXTO en pasos de planes multi-tool.

Raíz del bug: durante un plan multi-tool, cada nodo recibía el historial
completo de conversación (hasta 200 turnos) y "se acordaba" de tareas viejas,
ejecutando algo distinto (el paso 'guardar precio' terminó haciendo 'chmod'
de un script de una tarea anterior).

FIX: en planes con >1 paso, el executor/synthesizer inyectan un historial
RECORTADO (MAX_TURNOS_CONTEXTO_PLAN=0). El chat normal (1 paso) conserva todo.

NO requiere Ollama. Ejecutar: python tests/test_context_isolation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
import core.agent.tool_registry as tool_registry
from core.agent.graph_nodes import (
    node_plan_executor, _mem_recortada_para_plan, _mem_recortada_para_chat,
)
from core.agent.graph_state import crear_estado_inicial
from core.config.settings import MAX_TURNOS_CONTEXTO_CHAT

_TURNOS = [{"rol": "usuario", "texto": f"turno viejo {i}", "fecha": "x"} for i in range(30)]


class _node_patch:
    def __init__(self, func):
        self.func = func
    def __enter__(self):
        self.orig = tool_registry.get_node_func
        tool_registry.get_node_func = lambda tool: self.func
        return self
    def __exit__(self, *a):
        tool_registry.get_node_func = self.orig


def _run_paso(plan):
    visto = {}
    def captura(sub_state):
        visto["conversacion"] = list(sub_state["mem"].get("conversacion") or [])
        return {"final_response": "ok"}
    estado = crear_estado_inicial("orden", {"conversacion": list(_TURNOS)}, True)
    estado["plan_activo"] = True
    estado["plan_pasos"] = plan
    estado["plan_index"] = 0
    estado["plan_resultados"] = []
    with _node_patch(captura):
        node_plan_executor(estado)
    return visto, estado


# ──────────────────────────────────────────────────────────────────────

def test_multistep_recorta_historial():
    plan = [{"tool": "web", "instruccion": "a"}, {"tool": "shell", "instruccion": "b"}]
    visto, estado = _run_paso(plan)
    assert visto["conversacion"] == [], "multi-paso: el nodo NO debe ver historial viejo"
    # el original no se mutó
    assert len(estado["mem"]["conversacion"]) == 30


def test_chat_recorta_a_ventana():
    # Fix 2: un paso de CHARLA (modo_chat) recibe una ventana CHICA de historial
    # (MAX_TURNOS_CONTEXTO_CHAT), no los 30 turnos completos.
    plan = [{"tool": "text", "instruccion": "a", "modo_chat": True}]
    visto, estado = _run_paso(plan)
    assert len(visto["conversacion"]) == MAX_TURNOS_CONTEXTO_CHAT, (
        f"chat: debe recortar a {MAX_TURNOS_CONTEXTO_CHAT}, vio {len(visto['conversacion'])}"
    )
    # ventana = los turnos MÁS RECIENTES
    assert visto["conversacion"] == _TURNOS[-MAX_TURNOS_CONTEXTO_CHAT:]
    # el original no se mutó
    assert len(estado["mem"]["conversacion"]) == 30


def test_text_un_paso_sin_flag_tambien_recorta():
    # Robustez: aunque el paso text no traiga modo_chat, se trata como charla.
    plan = [{"tool": "text", "instruccion": "a"}]
    visto, _ = _run_paso(plan)
    assert len(visto["conversacion"]) == MAX_TURNOS_CONTEXTO_CHAT


def test_accion_un_paso_conserva_historial():
    # Una tool de ACCIÓN de 1 paso (no es chat) conserva el historial completo.
    plan = [{"tool": "launch", "instruccion": "abrir x"}]
    visto, _ = _run_paso(plan)
    assert len(visto["conversacion"]) == 30, "acción 1-paso: historial completo"


def test_mem_recortada_preserva_perfil_y_no_muta():
    mem = {
        "preferencias": {"nombre_usuario": "Thomas", "notas": ["x"]},
        "flatpaks": {"firefox": "org.mozilla.firefox"},
        "historial_comandos": [{"cmd": "ls"}],
        "conversacion": [{"rol": "u", "texto": "hola"}],
    }
    copia = _mem_recortada_para_plan(mem)
    assert copia["conversacion"] == []
    # mismas referencias → las escrituras de los nodos se propagan al original
    assert copia["preferencias"] is mem["preferencias"]
    assert copia["flatpaks"] is mem["flatpaks"]
    assert copia["historial_comandos"] is mem["historial_comandos"]
    # original intacto
    assert mem["conversacion"] == [{"rol": "u", "texto": "hola"}]


def test_escritura_en_paso_propaga_a_mem_original():
    plan = [{"tool": "web", "instruccion": "a"}, {"tool": "shell", "instruccion": "b"}]
    def writer(sub_state):
        sub_state["mem"]["historial_comandos"].append({"cmd": "nuevo"})
        return {"final_response": "ok"}
    estado = crear_estado_inicial("orden", {"conversacion": list(_TURNOS)}, True)
    estado["plan_activo"] = True
    estado["plan_pasos"] = plan
    estado["plan_index"] = 0
    estado["plan_resultados"] = []
    antes = len(estado["mem"]["historial_comandos"])
    with _node_patch(writer):
        node_plan_executor(estado)
    # aunque el historial conversacional se recorta, las escrituras a
    # historial_comandos del paso SÍ deben propagarse al mem real
    assert len(estado["mem"]["historial_comandos"]) == antes + 1


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
