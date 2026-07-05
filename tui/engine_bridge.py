"""
engine_bridge.py — Puente entre la TUI (Textual) y el motor LangGraph de Aether.

OPCIÓN A (mínima invasión): no tocamos graph_nodes.py. En vez de eso:

1. Replicamos las 3 líneas de inicialización que hacía AetherService
   (inicializar_db, cargar_memoria, actualizar_flatpaks) pero sin el lock de
   threading ni el resto del aparataje pensado para FastAPI multi-request.

2. Invocamos el grafo con `.stream(estado, stream_mode="updates")` en vez de
   `.invoke()`. Esto nos da, nodo por nodo, el dict parcial que cada uno
   retorna — exactamente lo que graph_state.py define (plan_pasos, plan_index,
   error_activo, etc.) sin tener que parsear nada.

3. Como los nodos además hacen print() y _llm_chat hace print(token, ...) para
   el streaming, redirigimos sys.stdout a un buffer mientras el grafo corre.
   Un hilo aparte va leyendo ese buffer línea por línea (o char por char para
   el streaming de tokens) y lo empuja a una cola (queue.Queue) que la TUI
   consume de forma asíncrona.

Esto es deliberadamente un puente "de afuera": si mañana migramos a la Opción
B (callbacks de estado reales en graph_nodes.py), este archivo es el único
que hay que reescribir — la TUI (app.py) no se entera del cambio porque
consume la misma interfaz pública: iter_eventos(orden).
"""

from __future__ import annotations

import io
import sys
import queue
import threading
import contextlib
from dataclasses import dataclass, field
from typing import Any, Iterator

PROJECT_ROOT_HINT = "Ajustá sys.path en run.py, no aquí."


# ══════════════════════════════════════════════════════════════════════
# EVENTOS QUE LA TUI CONSUME
# ══════════════════════════════════════════════════════════════════════

@dataclass
class NodeUpdateEvent:
    """Un nodo del grafo terminó. Viene de stream_mode='updates'."""
    nodo: str
    delta: dict[str, Any]


@dataclass
class StdoutLineEvent:
    """Una línea completa de stdout capturada (print() de algún nodo, con \\n)."""
    texto: str


@dataclass
class TokenEvent:
    """
    Fragmento de streaming de texto sin salto de línea (los tokens que
    _llm_chat va imprimiendo con print(t, end='', flush=True)). No es una
    línea completa — la TUI debe ir concatenándolos en el chat en vivo.
    """
    fragmento: str


@dataclass
class DoneEvent:
    """El grafo terminó. Trae la respuesta final."""
    respuesta: str


@dataclass
class ErrorEvent:
    """Excepción no controlada durante la ejecución del grafo."""
    mensaje: str


Evento = NodeUpdateEvent | StdoutLineEvent | TokenEvent | DoneEvent | ErrorEvent


# ══════════════════════════════════════════════════════════════════════
# ESTADO DEL MOTOR (memoria en RAM, igual que AetherService pero sin lock)
# ══════════════════════════════════════════════════════════════════════

@dataclass
class _MotorState:
    inicializado: bool = False
    mem: dict = field(default_factory=dict)


_motor = _MotorState()


def inicializar_motor() -> None:
    """
    Equivalente a AetherService.initialize(), sin threading.Lock:
    la TUI es un solo proceso interactivo, no hay requests concurrentes
    que proteger.

    Usa asegurar_esquema + normalizar_mem para garantizar que "conversacion"
    y otras claves siempre existan.
    """
    if _motor.inicializado:
        return

    from core.memory.memory_manager import asegurar_esquema, cargar_memoria, normalizar_mem
    from core.tools.flatpak_manager import actualizar_flatpaks

    asegurar_esquema()
    raw_mem = cargar_memoria()
    _motor.mem = normalizar_mem(raw_mem)

    # Sesión única por ejecución de la TUI
    import uuid
    if not _motor.mem.get("sesion_id"):
        _motor.mem["sesion_id"] = uuid.uuid4().hex

    actualizar_flatpaks(_motor.mem, salida_lista="")
    _motor.inicializado = True


class _QueueWriter(io.TextIOBase):
    """
    file-like object que reemplaza sys.stdout temporalmente.

    Patrones que maneja (todos provienen de graph_nodes.py):

    A) print("🔧 [PLAN EXECUTOR]: Paso 1/3")
       → write() llega con '\\n' al final → StdoutLineEvent al ProgressLog.

    B) print(t, end="", flush=True)  dentro de _on_token
       → write() llega SIN '\\n', múltiples veces → TokenEvent al chat.

    C) print("\\n🎙️  Aether: ", end="", flush=True)  — el PREFIJO que emite
       _on_token antes del primer token real.
       → Contiene '\\n' al INICIO + texto sin '\\n' al final. Sin fix,
         el '\\n' hacía que se tratara como línea de log vacía + los tokens
         subsiguientes se desincronizaban del buffer.
       FIX: el prefijo "🎙️" se detecta y se descarta silenciosamente;
       el chat en app.py ya agrega su propio prefijo al mostrar la respuesta.

    D) print(f"   ⏳ {i}...", end="\\r", flush=True)  — countdown de visión
       → end="\\r" (retorno de carro, NO '\\n'). Sin fix, los 5 countdowns
         se acumulan en el buffer y salen todos juntos cuando llega el
         próximo '\\n' (el del "DEBUG SQLITE:" de registrar_turno).
       FIX: '\\r' actúa como separador igual que '\\n', pero las líneas
         resultantes se descartan (no las queremos en el log ni en el chat).
    """

    # Prefijo que _on_token imprime antes del primer token real.
    # Lo ignoramos; app.py pone su propio "🎙️  Aether:" al mostrar la respuesta.
    _PREFIJO_AETHER = "🎙️"

    def __init__(self, q: "queue.Queue[Evento]"):
        self._q = q
        self._buffer = ""
        self._en_streaming = False   # True mientras estamos recibiendo tokens del LLM

    def write(self, s: str) -> int:
        if not s:
            return 0

        self._buffer += s

        # ── Caso A y D: hay separador de línea (\n o \r) ────────────────
        # \n → línea real de log
        # \r → línea de "sobreescritura" (countdown ⏳) → descartar
        if "\n" in self._buffer or "\r" in self._buffer:
            # Partir por ambos separadores, preservando cuál fue cuál
            import re as _re
            partes = _re.split(r"(\n|\r)", self._buffer)
            self._buffer = ""

            i = 0
            while i < len(partes):
                segmento = partes[i]
                sep = partes[i + 1] if i + 1 < len(partes) else None
                i += 2

                if sep == "\r":
                    # Countdown ⏳ o cualquier línea de "sobreescritura" → ignorar
                    self._en_streaming = False
                    continue

                # sep == "\n" o es el fragmento final sin separador
                texto = segmento.strip()

                if not texto:
                    # Línea vacía o solo whitespace → skip (pero puede marcar
                    # el fin del streaming si estábamos en él)
                    if sep == "\n":
                        self._en_streaming = False
                    continue

                if self._PREFIJO_AETHER in texto:
                    # Es el prefijo "🎙️  Aether: " que _on_token imprime antes
                    # del primer token. Lo descartamos; activamos modo streaming.
                    self._en_streaming = True
                    # Si hay texto DESPUÉS del prefijo en la misma línea
                    # (raro, pero por las dudas), emitirlo como TokenEvent.
                    after = texto.split(self._PREFIJO_AETHER, 1)[-1].lstrip(": ").strip()
                    if after:
                        self._q.put(TokenEvent(fragmento=after))
                    continue

                if sep == "\n":
                    # Línea completa → log de progreso
                    self._en_streaming = False
                    self._q.put(StdoutLineEvent(texto=segmento.rstrip("\r\n")))
                else:
                    # Fragmento final sin separador — puede ser residuo
                    self._buffer = segmento

            return len(s)

        # ── Caso B y C (parcial): fragmento sin ningún separador ────────
        # Son tokens del LLM llegando uno a uno con print(t, end="", flush=True).
        if self._buffer:
            self._q.put(TokenEvent(fragmento=self._buffer))
            self._buffer = ""
        return len(s)

    def flush(self) -> None:
        # No-op intencional. print(flush=True) llama aquí en cada token,
        # pero ya resolvemos todo en write(). No emitir nada aquí para
        # no duplicar eventos.
        pass

    def vaciar_residual(self) -> None:
        """Llamar UNA SOLA VEZ al terminar el grafo, para no perder el
        último fragmento que haya quedado sin separador en el buffer."""
        if self._buffer.strip():
            if self._en_streaming:
                self._q.put(TokenEvent(fragmento=self._buffer))
            else:
                self._q.put(StdoutLineEvent(texto=self._buffer))
        self._buffer = ""
        self._en_streaming = False


def _correr_grafo_en_hilo(orden: str, q: "queue.Queue[Evento]") -> None:
    """
    Ejecuta el grafo con stream_mode='updates' en un hilo separado,
    redirigiendo stdout a la cola mientras corre. Al terminar, mete un
    DoneEvent (o ErrorEvent si algo reventó).
    """
    from core.agent.graph_builder import get_graph
    from core.agent.graph_state import crear_estado_inicial
    from core.memory.memory_manager import registrar_turno

    writer = _QueueWriter(q)
    try:
        registrar_turno(_motor.mem, "usuario", orden)

        grafo = get_graph()
        estado = crear_estado_inicial(orden, _motor.mem, modo_autonomo=True)

        ultimo_estado: dict[str, Any] = dict(estado)

        with contextlib.redirect_stdout(writer):
            for update in grafo.stream(estado, stream_mode="updates"):
                # update es {nombre_nodo: dict_parcial} (uno por nodo que corrió)
                for nodo, delta in update.items():
                    if isinstance(delta, dict):
                        ultimo_estado.update(delta)
                    q.put(NodeUpdateEvent(nodo=nodo, delta=delta or {}))
            writer.vaciar_residual()

        respuesta = ultimo_estado.get("final_response") or "Operación completada."
        q.put(DoneEvent(respuesta=respuesta))

    except Exception as e:  # noqa: BLE001 — queremos capturar TODO para no tirar abajo la TUI
        writer.vaciar_residual()
        q.put(ErrorEvent(mensaje=str(e)))


def iter_eventos(orden: str) -> Iterator[Evento]:
    """
    Generador síncrono que la TUI consume desde un worker de Textual
    (@work(thread=True) o run_worker con thread=True).

    Internamente lanza un hilo que corre el grafo y va empujando eventos
    a una queue.Queue; este generador los va sacando y entregando uno a
    uno (bloqueante, pero se ejecuta en un worker thread, así que no
    congela la UI).
    """
    q: "queue.Queue[Evento]" = queue.Queue()
    hilo = threading.Thread(target=_correr_grafo_en_hilo, args=(orden, q), daemon=True)
    hilo.start()

    while True:
        evento = q.get()
        yield evento
        if isinstance(evento, (DoneEvent, ErrorEvent)):
            break