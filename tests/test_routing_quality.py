#!/usr/bin/env python3
"""
Tests de CALIDAD DE ROUTING (auditoría de razonamiento/selección de tools).

Cubre las mejoras implementadas tras la auditoría:
- Normalización de acentos: 'muestrame tu memoria' (sin tilde) → memory
  (antes caía a 'text' y el LLM alucinaba RAM/discos).
- Memoria del agente vs. variantes ('tu/mi memoria', 'qué recuerdas').
- Noticias en singular / eventos actuales → web (antes → text inventaba).
- Clasificador LLM como fallback cuando NO hay keyword (en vez de caer ciego
  a 'text'): permite routing por capacidad, p.ej. hardware real → shell.

NO requiere Ollama: se mockean _llm_chat / _clasificar_intent_llm.

Ejecutar: python tests/test_routing_quality.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
from core.agent.graph_nodes import (
    node_planner,
    _detectar_intent_keywords,
    _clasificar_intent_llm,
    _normalizar,
)


class _patch:
    def __init__(self, obj, attr, valor):
        self.obj, self.attr, self.valor = obj, attr, valor
    def __enter__(self):
        self.orig = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.valor)
        return self
    def __exit__(self, *a):
        setattr(self.obj, self.attr, self.orig)


def _tool(orden):
    return _detectar_intent_keywords(orden.lower())


# ──────────────────────────────────────────────────────────────────────
# Normalización de acentos (bug reportado: "muestrame tu memoria")
# ──────────────────────────────────────────────────────────────────────

def test_normalizar_quita_tildes():
    assert _normalizar("MUÉSTRAME") == "muestrame"
    assert _normalizar("última") == "ultima"
    assert _normalizar("qué pasó") == "que paso"


def test_memoria_sin_acento_va_a_memory():
    # EXACTAMENTE el caso reportado por el usuario
    assert _tool("muestrame tu memoria") == "memory"


def test_memoria_con_acento_va_a_memory():
    assert _tool("muéstrame tu memoria") == "memory"


def test_memoria_variantes_van_a_memory():
    for o in ("qué hay en tu memoria", "muéstrame mi memoria",
              "qué recuerdas de mí", "qué sabes de mí"):
        assert _tool(o) == "memory", o


# ──────────────────────────────────────────────────────────────────────
# Noticias / eventos actuales → web (antes inventaba en 'text')
# ──────────────────────────────────────────────────────────────────────

def test_noticia_singular_va_a_web():
    assert _tool("muéstrame una noticia") == "web"


def test_eventos_actuales_van_a_web():
    for o in ("qué pasó en el mundo", "cuéntame la última noticia de tecnología",
              "qué hay de nuevo", "cómo está el clima"):
        assert _tool(o) == "web", o


# ──────────────────────────────────────────────────────────────────────
# Clasificador LLM como fallback (routing por capacidad)
# ──────────────────────────────────────────────────────────────────────

def test_sin_keyword_usa_clasificador_llm():
    # 'describe mi configuración de hardware' no matchea keyword → clasificador
    # decide. Debe ir a shell (dato real del equipo), NO a text (alucinación).
    with _patch(gn, "_clasificar_intent_llm", lambda orden, mem: "shell"):
        upd = node_planner({"orden": "describe mi configuración de hardware", "mem": {}})
    assert upd["plan_pasos"][0]["tool"] == "shell"


def test_clasificador_extrae_palabra_valida():
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: "shell"):
        assert _clasificar_intent_llm("cuánta RAM tengo", {}) == "shell"


def test_clasificador_ignora_ruido_y_extrae_tool():
    # El LLM a veces responde con prosa; se extrae la primera tool válida
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: "Creo que es: web."):
        assert _clasificar_intent_llm("noticias", {}) == "web"


def test_clasificador_palabra_invalida_default_text():
    with _patch(gn, "_llm_chat", lambda system, user, on_token=None: "ninguna_tool"):
        assert _clasificar_intent_llm("hola", {}) == "text"


def test_clasificador_excepcion_default_text():
    def boom(system, user, on_token=None):
        raise RuntimeError("LLM caído")
    with _patch(gn, "_llm_chat", boom):
        assert _clasificar_intent_llm("hola", {}) == "text"


def test_node_memory_muestra_json_real_sin_acento():
    # "muestrame tu memoria" (sin tilde) debe VOLCAR el mem real (perfil),
    # nunca inventar hardware. Verifica la rama de display normalizada.
    from core.agent.graph_nodes import node_memory
    mem = {"preferencias": {"nombre_usuario": "Thomas", "navegador": "brave", "notas": ["x"]},
           "flatpaks": {}, "historial_comandos": [], "conversacion": []}
    out = node_memory({"orden": "muestrame tu memoria", "mem": mem})
    fr = out["final_response"]
    assert "nombre_usuario" in fr and "Thomas" in fr, fr
    # No debe contener specs de hardware inventadas
    assert "GB" not in fr.upper(), fr


def test_clasificador_solo_se_usa_si_no_hay_keyword():
    # Si hay keyword claro, el clasificador NO debe invocarse
    llamado = {"v": False}
    def marcar(orden, mem):
        llamado["v"] = True
        return "text"
    with _patch(gn, "_clasificar_intent_llm", marcar):
        upd = node_planner({"orden": "abre firefox", "mem": {}})
    assert upd["plan_pasos"][0]["tool"] == "launch"
    assert llamado["v"] is False, "no debe llamar al clasificador si hay keyword"


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
