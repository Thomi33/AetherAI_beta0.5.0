"""
Infraestructura pasiva para selección de modelos de Aether.

Esta primera versión NO cambia el comportamiento observable: toda decisión
devuelve el mismo modelo que el caller ya iba a usar. El objetivo es centralizar
el punto de decisión para poder incorporar costo de cambio, residencia,
frecuencia de uso y prioridad en una etapa posterior.
"""

from __future__ import annotations

from collections import deque
import time
from dataclasses import dataclass, field
from typing import Any, Literal


TaskKind = Literal[
    "chat",
    "planner",
    "synthesis",
    "shell",
    "code",
    "vision",
    "mcp",
    "error",
    "unknown",
]

Priority = Literal["latency", "normal", "quality"]
WouldDecision = Literal["switch", "stay"]


@dataclass(frozen=True)
class ModelDecisionContext:
    """Datos disponibles para decidir un modelo sin acoplarse al grafo."""

    task_kind: TaskKind = "unknown"
    requested_model: str = ""
    current_model: str | None = None
    prompt_chars: int = 0
    context_size: int | None = None
    priority: Priority = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelDecision:
    """Resultado de una decisión de modelo."""

    model: str
    reason: str
    switched: bool = False
    estimated_switch_cost_ms: int = 0
    recommended_model: str | None = None
    would_switch: bool = False
    would_decision: WouldDecision = "stay"
    observation_mode: bool = True


@dataclass(frozen=True)
class ModelPolicyMetric:
    """Métrica básica de una decisión/call de modelo."""

    ts: float
    task_kind: TaskKind
    active_model: str
    current_model: str | None
    recommended_model: str
    would_switch: bool
    would_decision: WouldDecision
    estimated_switch_cost_ms: int
    latency_ms: int | None = None
    prompt_chars: int = 0
    context_size: int | None = None
    priority: Priority = "normal"
    reason: str = ""


@dataclass
class ModelRuntimeState:
    """Estado liviano en memoria para futuras políticas adaptativas."""

    resident_model: str | None = None
    last_selected_model: str | None = None
    last_recommended_model: str | None = None
    last_task_kind: TaskKind = "unknown"
    last_decision_at: float = 0.0
    selection_counts: dict[str, int] = field(default_factory=dict)
    recommendation_counts: dict[str, int] = field(default_factory=dict)
    recent_metrics: deque[ModelPolicyMetric] = field(default_factory=lambda: deque(maxlen=200))


class ModelPolicy:
    """
    Política de modelos en modo compatibilidad.

    Por ahora actúa como pass-through estricto: si el caller pide `ornith:9b`,
    devuelve `ornith:9b`; si visión pide `qwen3-vl:8b`, devuelve `qwen3-vl:8b`.
    """

    def __init__(self) -> None:
        self.state = ModelRuntimeState()

    def choose(self, context: ModelDecisionContext) -> ModelDecision:
        active_model = context.requested_model
        current_model = context.current_model or self.state.last_selected_model
        recommended_model = self._recommend_model(context)
        estimated_switch_cost_ms = self._estimate_switch_cost_ms(
            current_model=current_model,
            recommended_model=recommended_model,
        )
        would_switch = bool(current_model and recommended_model != current_model)
        would_decision: WouldDecision = "switch" if would_switch else "stay"

        self.state.selection_counts[active_model] = self.state.selection_counts.get(active_model, 0) + 1
        self.state.recommendation_counts[recommended_model] = (
            self.state.recommendation_counts.get(recommended_model, 0) + 1
        )
        self.state.last_selected_model = active_model
        self.state.last_recommended_model = recommended_model
        self.state.last_task_kind = context.task_kind
        self.state.last_decision_at = time.time()
        if self.state.resident_model is None:
            self.state.resident_model = active_model

        reason = "observation_passthrough"
        self._append_metric(
            context=context,
            active_model=active_model,
            current_model=current_model,
            recommended_model=recommended_model,
            would_switch=would_switch,
            would_decision=would_decision,
            estimated_switch_cost_ms=estimated_switch_cost_ms,
            latency_ms=None,
            reason=reason,
        )

        return ModelDecision(
            model=active_model,
            reason=reason,
            switched=False,
            estimated_switch_cost_ms=estimated_switch_cost_ms,
            recommended_model=recommended_model,
            would_switch=would_switch,
            would_decision=would_decision,
            observation_mode=True,
        )

    def record_latency(self, decision: ModelDecision, latency_ms: int) -> None:
        """Completa la última métrica compatible con la decisión dada."""

        for idx in range(len(self.state.recent_metrics) - 1, -1, -1):
            metric = self.state.recent_metrics[idx]
            if (
                metric.active_model == decision.model
                and metric.recommended_model == (decision.recommended_model or decision.model)
                and metric.latency_ms is None
            ):
                self.state.recent_metrics[idx] = ModelPolicyMetric(
                    ts=metric.ts,
                    task_kind=metric.task_kind,
                    active_model=metric.active_model,
                    current_model=metric.current_model,
                    recommended_model=metric.recommended_model,
                    would_switch=metric.would_switch,
                    would_decision=metric.would_decision,
                    estimated_switch_cost_ms=metric.estimated_switch_cost_ms,
                    latency_ms=latency_ms,
                    prompt_chars=metric.prompt_chars,
                    context_size=metric.context_size,
                    priority=metric.priority,
                    reason=metric.reason,
                )
                return

    def _recommend_model(self, context: ModelDecisionContext) -> str:
        """
        Recomendación sombra.

        En esta etapa la recomendación se mantiene deliberadamente alineada con
        el modelo solicitado para conservar compatibilidad total.
        """

        return context.requested_model

    @staticmethod
    def _estimate_switch_cost_ms(current_model: str | None, recommended_model: str) -> int:
        if not current_model or current_model == recommended_model:
            return 0
        return 3000

    def _append_metric(
        self,
        context: ModelDecisionContext,
        active_model: str,
        current_model: str | None,
        recommended_model: str,
        would_switch: bool,
        would_decision: WouldDecision,
        estimated_switch_cost_ms: int,
        latency_ms: int | None,
        reason: str,
    ) -> None:
        self.state.recent_metrics.append(ModelPolicyMetric(
            ts=time.time(),
            task_kind=context.task_kind,
            active_model=active_model,
            current_model=current_model,
            recommended_model=recommended_model,
            would_switch=would_switch,
            would_decision=would_decision,
            estimated_switch_cost_ms=estimated_switch_cost_ms,
            latency_ms=latency_ms,
            prompt_chars=context.prompt_chars,
            context_size=context.context_size,
            priority=context.priority,
            reason=reason,
        ))


_policy = ModelPolicy()


def choose_model(context: ModelDecisionContext) -> ModelDecision:
    """Punto único de entrada para decisiones de modelo."""

    return _policy.choose(context)


def record_model_latency(decision: ModelDecision, latency_ms: int) -> None:
    """Registrar latencia observada sin influir en la decisión activa."""

    _policy.record_latency(decision, latency_ms)


def get_model_runtime_state() -> ModelRuntimeState:
    """Exponer estado para tests, diagnósticos futuros y UI."""

    return _policy.state


def reset_model_runtime_state() -> None:
    """Resetear estado en memoria para tests o diagnósticos controlados."""

    _policy.state = ModelRuntimeState()
