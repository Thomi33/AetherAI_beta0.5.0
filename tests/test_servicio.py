#!/usr/bin/env python3
"""
Tests Task 7: migración del CLI/servicio al motor LangGraph.

Ejecutar: python tests/test_servicio.py
No requiere Ollama: se mockea el grafo y la persistencia.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.services.graph_service as svc
from core.memory.memory_manager import normalizar_mem


class _FakeGraph:
    def __init__(self, final_response):
        self._fr = final_response
        self.invocado_con = None

    def invoke(self, estado):
        self.invocado_con = estado
        return {**estado, "final_response": self._fr}


def test_procesar_orden_grafo_usa_grafo_y_factory():
    import core.agent.graph_builder as gb

    fake = _FakeGraph("respuesta del grafo")
    turnos = []

    orig_get_graph = gb.get_graph
    orig_registrar = svc.registrar_turno
    gb.get_graph = lambda: fake
    svc.registrar_turno = lambda mem, rol, texto: turnos.append((rol, texto))
    try:
        mem = {}
        out = svc.procesar_orden_grafo("hola", mem, modo_autonomo=True)
    finally:
        gb.get_graph = orig_get_graph
        svc.registrar_turno = orig_registrar

    assert out == "respuesta del grafo"
    # Registró el turno del usuario
    assert ("usuario", "hola") in turnos
    # El estado pasado al grafo está completo y con mem normalizada
    estado = fake.invocado_con
    assert estado["orden"] == "hola"
    assert "preferencias" in estado["mem"]
    assert estado["plan_activo"] is False


def test_procesar_orden_grafo_respuesta_vacia_tiene_default():
    import core.agent.graph_builder as gb

    fake = _FakeGraph(None)
    orig_get_graph = gb.get_graph
    orig_registrar = svc.registrar_turno
    gb.get_graph = lambda: fake
    svc.registrar_turno = lambda *a, **k: None
    try:
        out = svc.procesar_orden_grafo("algo", {}, True)
    finally:
        gb.get_graph = orig_get_graph
        svc.registrar_turno = orig_registrar

    assert isinstance(out, str) and out.strip()


def test_process_message_usa_grafo():
    # Importar el servicio backend y forzar estado inicializado
    from backend.core import aether_service as backend_svc

    backend_svc._AETHER_INITIALIZED = True
    backend_svc._AETHER_MEMORY = normalizar_mem({})

    llamadas = {}

    def fake_procesar(msg, mem, modo_autonomo=True):
        llamadas["msg"] = msg
        llamadas["mem_tiene_prefs"] = "preferencias" in mem
        return "ok desde grafo"

    orig = svc.procesar_orden_grafo
    svc.procesar_orden_grafo = fake_procesar
    try:
        res = backend_svc.AetherService.process_message("buenos días")
    finally:
        svc.procesar_orden_grafo = orig

    assert res["agent_status"] == "ready"
    assert res["response"] == "ok desde grafo"
    assert llamadas["msg"] == "buenos días"
    assert llamadas["mem_tiene_prefs"] is True


def test_process_message_memoria_persiste_entre_llamadas():
    from backend.core import aether_service as backend_svc

    backend_svc._AETHER_INITIALIZED = True
    backend_svc._AETHER_MEMORY = normalizar_mem({})

    def fake_procesar(msg, mem, modo_autonomo=True):
        # Simula que el grafo agrega un turno a la conversación
        mem["conversacion"].append({"rol": "usuario", "texto": msg})
        return "ok"

    orig = svc.procesar_orden_grafo
    svc.procesar_orden_grafo = fake_procesar
    try:
        backend_svc.AetherService.process_message("uno")
        backend_svc.AetherService.process_message("dos")
    finally:
        svc.procesar_orden_grafo = orig

    convo = backend_svc._AETHER_MEMORY["conversacion"]
    textos = [t["texto"] for t in convo]
    assert "uno" in textos and "dos" in textos


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
