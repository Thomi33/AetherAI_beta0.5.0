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
    for clave in ("conversacion", "historial_comandos", "core", "sesion_id"):
        assert clave in upd["mem"]


# ──────────────────────────────────────────────────────────────────────
# PROBLEMA 2 — multi-tool: el planner activa un plan de >1 paso
# ──────────────────────────────────────────────────────────────────────

_ORDEN_MULTI = "busca el precio de bitcoin y luego captura la pantalla"


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


# ═════════════════════════════════════════════════════════════════════
# ANAFÓRICA (CASO 0 refactor) — tests de las dos etapas + generalización
# Se mockean _confirmar y/o _llm_chat + se arma mem con historial.
# ═════════════════════════════════════════════════════════════════════

def _mk_mem_con_turno(rol="jarvis", texto="", tema="shell", sesion_id="s1"):
    return {
        "conversacion": [
            {"rol": rol, "texto": texto, "tema": tema, "sesion_id": sesion_id, "fecha": "2026-01-01"}
        ],
        "sesion_id": sesion_id,
        "preferencias": {},
        "flatpaks": [],
        "historial_comandos": [],
        "core": {},
    }


def test_posible_referencia_detecta_keywords():
    assert gn._posible_referencia_anaforica("ejecutalo") is True
    assert gn._posible_referencia_anaforica("Dale") is True
    assert gn._posible_referencia_anaforica("hacélo de nuevo") is True
    assert gn._posible_referencia_anaforica("guardalo") is True
    # no debe activar en órdenes normales
    assert gn._posible_referencia_anaforica("abre firefox") is False
    assert gn._posible_referencia_anaforica("busca el precio") is False


def test_confirmar_llm_fewshot_ejecutalo_si():
    # Mockeamos _llm_chat dentro del módulo para que _confirmar responda 'si'
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: "si"):
        assert gn._confirmar_referencia_anaforica_llm("ejecutalo") is True
        assert gn._confirmar_referencia_anaforica_llm("dale nomás") is True


def test_confirmar_llm_fewshot_autosuficiente_no():
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: "no"):
        # El caso clásico que rompía keywords puros
        assert gn._confirmar_referencia_anaforica_llm(
            "Buscá el comando para limpiar la caché de pip y ejecutálo"
        ) is False
        assert gn._confirmar_referencia_anaforica_llm(
            "dale doble click al ícono de la papelera"
        ) is False


def test_planner_anaf_ejecutalo_resuelve_shell_directo():
    # Caso feliz: "ejecutalo" + antecedente con [SHELL] → usa args["command"] sin LLM
    mem = _mk_mem_con_turno(
        texto="Listo, podés correr `[SHELL]pip cache purge[/SHELL]` para limpiar.",
        tema="shell",
    )
    with _patch(gn, "_confirmar_referencia_anaforica_llm", lambda orden: True):
        upd = node_planner({"orden": "ejecutalo", "mem": mem, "sesion_id": "s1"})
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) == 1
    paso = upd["plan_pasos"][0]
    assert paso["tool"] == "shell"
    assert paso["args"]["command"] == "pip cache purge"


def test_planner_anaf_sin_antecedente_pide_aclaracion():
    # "dale" pero último turno de Aether no proponía nada ejecutable
    mem = _mk_mem_con_turno(texto="No tengo nada que ejecutar ahora mismo.", tema="text")
    with _patch(gn, "_confirmar_referencia_anaforica_llm", lambda orden: True):
        upd = node_planner({"orden": "dale", "mem": mem, "sesion_id": "s1"})
    assert upd.get("plan_activo") is False
    assert "final_response" in upd
    assert "con qué exactamente" in (upd.get("final_response") or "").lower()


def test_planner_anaf_autosuficiente_con_keyword_no_toma_rama_anaf():
    # El bug histórico: contiene "ejecutálo" pero la orden es completa → no anaf
    mem = _mk_mem_con_turno(texto="nada relevante", tema="web")
    with _patch(gn, "_confirmar_referencia_anaforica_llm", lambda orden: False):
        # No debe tomar la rama anaf aunque el posible diera true
        upd = node_planner({
            "orden": "Buscá el comando para limpiar la caché de pip y ejecutálo",
            "mem": mem,
            "sesion_id": "s1"
        })
    # Como no es anaf (confirm=false), debe caer a keyword (web por "buscá")
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) == 1
    # 'buscá' → web (launch_excluye no aplica aquí)
    assert upd["plan_pasos"][0]["tool"] in ("web", "shell", "text")


def test_planner_anaf_generalizado_guardalo_filewrite_prefill():
    # Generalización: "guardalo" ref a web previo → plan file_write con prefill
    mem = _mk_mem_con_turno(
        texto="El precio del BTC es aproximadamente 67000 USD según fuentes recientes.",
        tema="web",
    )
    with _patch(gn, "_confirmar_referencia_anaforica_llm", lambda o: True):
        upd = node_planner({"orden": "guardalo en btc.txt", "mem": mem, "sesion_id": "s1"})
    assert upd["plan_activo"] is True
    assert len(upd["plan_pasos"]) == 1
    assert upd["plan_pasos"][0]["tool"] == "file_write"
    assert "plan_resultados" in upd
    assert "67000" in str(upd.get("plan_resultados", [""])[0])


def test_planner_anaf_mandaselo_sin_detalle_text_o_aclaracion():
    # "mandaselo" sin decir qué ni a quién → si hay payload pero no command específico,
    # vamos a text (o aclaración si no hay last_texto usable). Aquí hay last → text.
    mem = _mk_mem_con_turno(texto="Encontré el archivo foo.log.", tema="web")
    with _patch(gn, "_confirmar_referencia_anaforica_llm", lambda o: True):
        upd = node_planner({"orden": "mandaselo", "mem": mem, "sesion_id": "s1"})
    assert upd["plan_activo"] is True
    assert upd["plan_pasos"][0]["tool"] == "text"


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
