"""Selección de modelos: pass-through puro.

Toda decisión devuelve el modelo que el caller ya iba a usar. El punto de
decisión queda centralizado para poder incorporar políticas (costo de cambio,
residencia, prioridad) en el futuro, pero hoy no agrega comportamiento:
choose_model() es la identidad sobre `requested_model` y la medición de
latencia es un no-op (sin consumidores).
"""

from dataclasses import dataclass
from typing import Literal

TaskKind = Literal[
    "chat", "planner", "synthesis", "shell", "code",
    "vision", "mcp", "error", "unknown",
]
Priority = Literal["latency", "normal", "quality"]


@dataclass(frozen=True)
class ModelDecisionContext:
    """Datos disponibles para decidir un modelo sin acoplarse al grafo."""

    task_kind: TaskKind = "unknown"
    requested_model: str = ""
    prompt_chars: int = 0
    context_size: int | None = None


@dataclass(frozen=True)
class ModelDecision:
    """Resultado de una decisión de modelo."""

    model: str
    reason: str = "pass-through"
    switched: bool = False


def choose_model(context: ModelDecisionContext) -> ModelDecision:
    """Punto único de entrada: devuelve siempre el modelo solicitado."""
    return ModelDecision(model=context.requested_model)


def record_model_latency(decision: ModelDecision, latency_ms: int) -> None:
    """No-op: la medición pasiva no tiene consumidores actuales."""