#!/usr/bin/env python3
"""
Tests de la infraestructura pasiva de selección de modelos.

La etapa inicial debe ser 100% compatible: la policy devuelve exactamente el
modelo que el caller ya iba a usar.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent.model_policy import (
    ModelDecisionContext,
    choose_model,
    get_model_runtime_state,
    record_model_latency,
    reset_model_runtime_state,
)


def test_model_policy_passthrough_text_model():
    reset_model_runtime_state()
    decision = choose_model(ModelDecisionContext(
        task_kind="chat",
        requested_model="ornith:9b",
        prompt_chars=123,
        context_size=8192,
    ))

    assert decision.model == "ornith:9b"
    assert decision.recommended_model == "ornith:9b"
    assert decision.reason == "observation_passthrough"
    assert decision.switched is False
    assert decision.observation_mode is True
    assert decision.would_decision == "stay"


def test_model_policy_passthrough_vision_model():
    reset_model_runtime_state()
    decision = choose_model(ModelDecisionContext(
        task_kind="vision",
        requested_model="qwen3-vl:8b",
        prompt_chars=456,
        context_size=8192,
    ))

    assert decision.model == "qwen3-vl:8b"
    assert decision.recommended_model == "qwen3-vl:8b"
    assert decision.reason == "observation_passthrough"
    assert decision.switched is False
    assert decision.would_decision == "stay"


def test_model_policy_records_observation_metrics():
    reset_model_runtime_state()
    decision = choose_model(ModelDecisionContext(
        task_kind="vision",
        requested_model="qwen3-vl:8b",
        current_model="ornith:9b",
        prompt_chars=456,
        context_size=8192,
        priority="quality",
    ))
    record_model_latency(decision, 42)

    state = get_model_runtime_state()
    metric = state.recent_metrics[-1]

    assert decision.model == "qwen3-vl:8b"
    assert decision.recommended_model == "qwen3-vl:8b"
    assert decision.would_switch is True
    assert decision.would_decision == "switch"
    assert decision.estimated_switch_cost_ms > 0
    assert metric.active_model == "qwen3-vl:8b"
    assert metric.current_model == "ornith:9b"
    assert metric.recommended_model == "qwen3-vl:8b"
    assert metric.task_kind == "vision"
    assert metric.would_decision == "switch"
    assert metric.latency_ms == 42
    assert metric.priority == "quality"


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
