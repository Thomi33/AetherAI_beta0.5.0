#!/usr/bin/env python3
"""
Tests Fix 7 + Fix 1: fast-path determinista + intent gate LLM en node_planner.

Flujo de decisión del planner para input SIN keyword accionable:
  1. FAST-PATH determinista (_es_charla_trivial): saludo/ack/small talk →
     text + modo_chat, SIN LLM.
  2. Si el fast-path no matchea → INTENT GATE LLM (_clasificar_intent_llm)
     razona si hace falta tool o es charla.

Estos tests verifican, sin Ollama:
  - el fast-path atrapa charla trivial SIN invocar el gate;
  - el gate LLM solo corre si no hubo keyword NI fast-path;
  - parsing robusto del gate;
  - modo_chat se marca cuando el resultado es text (por fast-path o por gate).

Ejecutar: python tests/test_chat_gating.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import node_planner, _clasificar_intent_llm, _es_charla_trivial
from core.agent.graph_state import crear_estado_inicial


# ── 1. Fast-path determinista (_es_charla_trivial) ────────────────────

_CHARLA_SI = [
    "Hola", "hola!", "Hola, ¿cómo estás?", "buenas", "buenos días",
    "buenas tardes", "Hola buenas", "hola que tal", "qué tal", "qué onda",
    "gracias", "muchas gracias", "ok gracias", "dale gracias",
    "ok", "okay", "dale", "vale", "listo", "perfecto", "genial", "claro",
    "ok dale", "sí gracias", "no gracias", "perfecto gracias",
    "chau", "adiós", "nos vemos", "hasta luego", "todo bien",
]

_CHARLA_NO = [
    "abre firefox", "busca el precio del dólar", "muéstrame tu memoria",
    "no abras firefox", "matar el proceso 1234", "reinicia el servicio de red",
    "borra la base de datos de prod", "instala el paquete neovim",
    "cuéntame algo interesante por favor", "qué opinas sobre la inteligencia artificial",
    "necesito que me expliques cómo funciona esto en detalle", "",
]


def test_fastpath_positivos():
    fallidos = [t for t in _CHARLA_SI if not _es_charla_trivial(t)]
    assert not fallidos, f"deberían ser charla trivial: {fallidos}"


def test_fastpath_negativos():
    fallidos = [t for t in _CHARLA_NO if _es_charla_trivial(t)]
    assert not fallidos, f"NO deberían ser charla trivial: {fallidos}"


# ── 2. Parsing del intent gate (mock de _llm_chat) ────────────────────

def _con_llm_chat(retorno):
    original = gn._llm_chat
    if isinstance(retorno, Exception):
        def fake(*a, **k):
            raise retorno
    else:
        def fake(*a, **k):
            return retorno
    gn._llm_chat = fake
    return original


def test_gate_parsea_y_defaultea():
    casos = {
        "shell": "shell", "  WEB\n": "web", "Text.": "text",
        "la herramienta adecuada es shell": "shell",  # extrae 1ra tool válida
        "ninguna idea": "text",                       # sin tool → default text
    }
    for salida_llm, esperado in casos.items():
        original = _con_llm_chat(salida_llm)
        try:
            assert _clasificar_intent_llm("x", {}) == esperado, (salida_llm, esperado)
        finally:
            gn._llm_chat = original


def test_gate_excepcion_cae_a_text():
    original = _con_llm_chat(RuntimeError("ollama caído"))
    try:
        assert _clasificar_intent_llm("x", {}) == "text"
    finally:
        gn._llm_chat = original


# ── 3. Integración: fast-path vs gate vs keyword ──────────────────────

class _ContadorGate:
    def __init__(self, retorno="text"):
        self.llamadas = 0
        self.retorno = retorno

    def __call__(self, orden, mem):
        self.llamadas += 1
        return self.retorno


def _planificar(orden, retorno_gate="text"):
    estado = crear_estado_inicial(orden, {}, True)
    contador = _ContadorGate(retorno_gate)
    original = gn._clasificar_intent_llm
    gn._clasificar_intent_llm = contador
    try:
        out = node_planner(estado)
    finally:
        gn._clasificar_intent_llm = original
    return out, contador


def test_fastpath_no_invoca_gate():
    # Charla trivial → fast-path → text + modo_chat, SIN tocar el gate LLM.
    for orden in ("Hola", "buenos días", "gracias", "ok dale", "perfecto gracias"):
        out, contador = _planificar(orden, retorno_gate="shell")  # retorno no debería usarse
        assert contador.llamadas == 0, f"'{orden}' NO debería invocar el gate (fast-path)"
        assert out["plan_pasos"][0]["tool"] == "text", orden
        assert out["plan_pasos"][0].get("modo_chat") is True, orden


def test_gate_se_usa_si_no_hay_fastpath_ni_keyword():
    # Conversacional pero NO trivial → cae al gate LLM (mock → text) con modo_chat.
    for orden in ("contame un chiste", "¿qué opinás de esto en general?", "no te entendí, repetí eso"):
        out, contador = _planificar(orden, retorno_gate="text")
        assert contador.llamadas == 1, f"'{orden}' debería consultar al gate"
        assert out["plan_pasos"][0]["tool"] == "text"
        assert out["plan_pasos"][0].get("modo_chat") is True


def test_gate_decide_tool_no_marca_modo_chat():
    out, contador = _planificar("fijate cuánta memoria queda libre", retorno_gate="shell")
    assert contador.llamadas == 1
    assert out["plan_pasos"][0]["tool"] == "shell"
    assert out["plan_pasos"][0].get("modo_chat") is None


def test_keyword_no_invoca_fastpath_ni_gate():
    for orden, tool in (("abre firefox", "launch"), ("busca el precio del dólar", "web")):
        out, contador = _planificar(orden, retorno_gate="shell")
        assert contador.llamadas == 0, f"'{orden}' no debería invocar el gate"
        assert out["plan_pasos"][0]["tool"] == tool
        assert out["plan_pasos"][0].get("modo_chat") is None


def test_plan_siempre_activo():
    out, _ = _planificar("hola", retorno_gate="text")
    assert out["plan_activo"] is True
    assert out["plan_index"] == 0
    assert out["plan_resultados"] == []


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
