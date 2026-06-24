#!/usr/bin/env python3
"""
Tests de la PUERTA DE DECISIÓN del grafo: node_planner / _planner_llm.

Cubre del diagnóstico:
- PROBLEMA 2: el planner SIEMPRE produce un plan (plan_activo=True).
- PROBLEMA 4: validación de JSON del planner + fallback ante prosa/JSON inválido.
- PROBLEMA 8: backwards-compat — órdenes simples → plan de 1 paso con la tool correcta.

NO requiere Ollama: se mockean _llm_chat y _planner_llm.

Ejecutar: python tests/test_planner_decision.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import (
    node_planner,
    _planner_llm,
    _extraer_json_objeto,
    _parece_multitool,
)


# ──────────────────────────────────────────────────────────────────────
# Helper de mock
# ──────────────────────────────────────────────────────────────────────

class _patch:
    """Context manager mínimo para monkeypatch de atributos de módulo."""
    def __init__(self, obj, attr, valor):
        self.obj, self.attr, self.valor = obj, attr, valor
    def __enter__(self):
        self.orig = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.valor)
        return self
    def __exit__(self, *a):
        setattr(self.obj, self.attr, self.orig)


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 8 — backwards-compat: single-tool por keywords (sin LLM)
# ──────────────────────────────────────────────────────────────────────

def test_single_tool_texto_simple():
    # 'hola...' no matchea keyword → ahora se consulta al clasificador LLM
    # (mockeado aquí). Antes caía ciego a 'text'.
    with _patch(gn, "_clasificar_intent_llm", lambda orden, mem: "text"):
        upd = node_planner({"orden": "hola, cómo estás", "mem": {}})
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) == 1
    assert upd["plan_pasos"][0]["tool"] == "text"


def test_single_tool_launch():
    upd = node_planner({"orden": "abre firefox", "mem": {}})
    assert len(upd["plan_pasos"]) == 1
    assert upd["plan_pasos"][0]["tool"] == "launch"


def test_single_tool_web():
    upd = node_planner({"orden": "busca el clima de hoy", "mem": {}})
    assert len(upd["plan_pasos"]) == 1
    assert upd["plan_pasos"][0]["tool"] == "web"


def test_single_tool_vision():
    upd = node_planner({"orden": "mira la pantalla y dime qué ves", "mem": {}})
    assert len(upd["plan_pasos"]) == 1
    assert upd["plan_pasos"][0]["tool"] == "vision"


def test_single_tool_memory():
    upd = node_planner({"orden": "qué recuerdas de mí", "mem": {}})
    assert len(upd["plan_pasos"]) == 1
    assert upd["plan_pasos"][0]["tool"] == "memory"


def test_planner_propaga_mem_normalizada():
    # PROBLEMA 1: aunque mem venga vacío, el planner lo normaliza y propaga
    upd = node_planner({"orden": "hola", "mem": {}})
    assert "mem" in upd
    for clave in ("preferencias", "flatpaks", "historial_comandos", "conversacion"):
        assert clave in upd["mem"]


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 2 — multi-tool: el planner activa un plan de >1 paso
# ──────────────────────────────────────────────────────────────────────

_ORDEN_MULTI = "busca el precio de bitcoin y guarda el resultado en un archivo"


def test_orden_parece_multitool():
    assert _parece_multitool(_ORDEN_MULTI) is True


def test_multi_tool_plan_valido_se_activa():
    plan_fake = [
        {"tool": "web", "instruccion": "buscar precio bitcoin", "args": {"query": "precio bitcoin"}},
        {"tool": "shell", "instruccion": "guardar en archivo", "args": {"command": "echo x > p.txt"}},
    ]
    with _patch(gn, "_planner_llm", lambda orden, mem: plan_fake):
        upd = node_planner({"orden": _ORDEN_MULTI, "mem": {}})
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) == 2
    assert [p["tool"] for p in upd["plan_pasos"]] == ["web", "shell"]


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 4 — fallback robusto cuando el LLM no da un plan usable
# ──────────────────────────────────────────────────────────────────────

def test_multi_tool_llm_devuelve_none_cae_a_fallback():
    with _patch(gn, "_planner_llm", lambda orden, mem: None):
        upd = node_planner({"orden": _ORDEN_MULTI, "mem": {}})
    # Fallback seguro: sigue habiendo un plan (de 1 paso), nunca explota
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) == 1


def test_multi_tool_plan_invalido_cae_a_fallback():
    # plan con tool inexistente → validar_plan falla → fallback 1 paso
    plan_malo = [{"tool": "inexistente", "instruccion": "x"}]
    with _patch(gn, "_planner_llm", lambda orden, mem: plan_malo):
        upd = node_planner({"orden": _ORDEN_MULTI, "mem": {}})
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) == 1
    assert upd["plan_pasos"][0]["tool"] in ("web", "text")


def test_planner_nunca_lanza_aunque_llm_explote():
    def boom(orden, mem):
        raise RuntimeError("LLM caído")
    with _patch(gn, "_planner_llm", boom):
        # node_planner llama a _planner_llm sólo si _parece_multitool;
        # si _planner_llm lanza, node_planner NO debe propagar la excepción.
        try:
            upd = node_planner({"orden": _ORDEN_MULTI, "mem": {}})
        except Exception as e:
            assert False, f"node_planner no debe lanzar: {e}"
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) >= 1


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 4 — _planner_llm: parsing de JSON robusto
# ──────────────────────────────────────────────────────────────────────

def test_planner_llm_json_limpio():
    resp = '{"multi_tool": true, "pasos": [{"tool": "web", "instruccion": "buscar"}]}'
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: resp):
        pasos = _planner_llm("orden", {})
    assert pasos is not None and len(pasos) == 1
    assert pasos[0]["tool"] == "web"


def test_planner_llm_json_en_fence():
    resp = 'Claro:\n```json\n{"multi_tool": true, "pasos": [{"tool": "shell", "instruccion": "ls"}]}\n```'
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: resp):
        pasos = _planner_llm("orden", {})
    assert pasos is not None and pasos[0]["tool"] == "shell"


def test_planner_llm_json_embebido_en_prosa():
    # Caso del diagnóstico: "Claro, voy a crear un plan..." + objeto JSON
    resp = 'Claro, voy a crear un plan para ti: {"multi_tool": true, "pasos": [{"tool": "web", "instruccion": "x"}]} ¡Listo!'
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: resp):
        pasos = _planner_llm("orden", {})
    assert pasos is not None and pasos[0]["tool"] == "web"


def test_planner_llm_prosa_pura_retorna_none():
    # PROBLEMA 4: prosa sin JSON → None (y se loguea el contenido)
    resp = "Claro, voy a crear un plan para resolver tu tarea paso a paso."
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: resp):
        pasos = _planner_llm("orden", {})
    assert pasos is None


def test_planner_llm_multi_tool_false_retorna_none():
    resp = '{"multi_tool": false}'
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: resp):
        pasos = _planner_llm("orden", {})
    assert pasos is None


def test_extraer_json_objeto_balanceado():
    # llaves dentro de strings no deben romper el balanceo
    txt = 'ruido {"a": "valor con } llave", "b": {"c": 1}} cola'
    data = _extraer_json_objeto(txt)
    assert data == {"a": "valor con } llave", "b": {"c": 1}}


def test_extraer_json_objeto_sin_json():
    assert _extraer_json_objeto("no hay json aquí") is None


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
