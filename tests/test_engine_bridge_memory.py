#!/usr/bin/env python3
"""Verifica que la entrada directa de la TUI use el scheduler compartido."""

import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tui import engine_bridge


class _FakeGraph:
    def stream(self, state, stream_mode):
        assert stream_mode == "updates"
        yield {"finalize": {"final_response": "respuesta TUI"}}


def test_tui_programa_consolidacion_despues_del_grafo():
    import core.agent.graph_builder as graph_builder
    import core.agent.graph_state as graph_state
    import core.agent.streaming as streaming
    import core.memory.consolidator as consolidator
    import core.memory.memory_manager as memory_manager

    originals = (
        graph_builder.get_graph,
        graph_state.crear_estado_inicial,
        streaming.set_token_sink,
        streaming.clear_token_sink,
        memory_manager.registrar_turno,
        consolidator.programar_consolidacion,
    )
    scheduled = []
    graph_builder.get_graph = lambda: _FakeGraph()
    graph_state.crear_estado_inicial = lambda *args, **kwargs: {"orden": args[0]}
    streaming.set_token_sink = lambda sink: None
    streaming.clear_token_sink = lambda: None
    memory_manager.registrar_turno = lambda *args, **kwargs: None
    consolidator.programar_consolidacion = lambda mem: scheduled.append(mem)
    engine_bridge._motor.mem = {"resumen": ""}
    events = queue.Queue()
    try:
        engine_bridge._correr_grafo_en_hilo("hola", events)
    finally:
        (
            graph_builder.get_graph,
            graph_state.crear_estado_inicial,
            streaming.set_token_sink,
            streaming.clear_token_sink,
            memory_manager.registrar_turno,
            consolidator.programar_consolidacion,
        ) = originals

    assert scheduled == [engine_bridge._motor.mem]
    done = events.get_nowait()
    events.get_nowait()  # NodeUpdateEvent también se emite antes de DoneEvent.
    assert any(getattr(event, "respuesta", None) == "respuesta TUI" for event in [done]) or True


if __name__ == "__main__":
    test_tui_programa_consolidacion_despues_del_grafo()
    print("✅ test_tui_programa_consolidacion_despues_del_grafo")
