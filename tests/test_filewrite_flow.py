#!/usr/bin/env python3
"""
Tests del flujo "buscar y GUARDAR" multi-tool (web → file_write).

BUG: el planner usaba 'shell' para guardar; node_shell llamaba al LLM y se
contaminaba con el historial (ejecutaba una tarea vieja). FIX: el planner usa
'file_write', que escribe plan_resultados[-1] SIN llamar al LLM (determinista,
sin contaminación).

NO requiere Ollama: se mockean _planner_llm y el nodo web.

Ejecutar: python tests/test_filewrite_flow.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn
import core.agent.tool_registry as tool_registry
from core.agent.graph_nodes import node_planner, node_file_write, node_plan_executor, _construir_orden_paso
from core.agent.graph_state import crear_estado_inicial
from core.config.settings import CARPETA_AETHER


class _patch:
    def __init__(self, obj, attr, valor):
        self.obj, self.attr, self.valor = obj, attr, valor
    def __enter__(self):
        self.orig = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.valor)
        return self
    def __exit__(self, *a):
        setattr(self.obj, self.attr, self.orig)


_ORDEN = "buscá el precio de bitcoin y guardalo en un archivo"


def test_escribir_archivo_nombre_suelto_va_a_carpeta_aether():
    from core.tools.file_writer import _resolver_destino
    destino = _resolver_destino("guardar en notas_test.md")
    assert destino == os.path.join(str(CARPETA_AETHER), "notas_test.md")


def test_escribir_archivo_respeta_ruta_absoluta_y_tilde():
    from core.tools.file_writer import _resolver_destino
    destino = _resolver_destino("guardalo en ~/Documentos/x.txt")
    assert destino == os.path.join(os.path.expanduser("~"), "Documentos", "x.txt")
    assert _resolver_destino("guardalo en /tmp/aether_ruta_test.txt") == "/tmp/aether_ruta_test.txt"


def test_resolver_destino_ignora_urls_del_contexto():
    from core.tools.file_writer import _resolver_destino
    orden = ("guardar en precio.txt\n[CONTEXTO DE PASOS PREVIOS]\n"
             "  - Paso 1: ver https://site.com/page.html para más info")
    assert _resolver_destino(orden) == os.path.join(str(CARPETA_AETHER), "precio.txt")


def test_planner_enruta_guardar_a_file_write():
    plan = [
        {"tool": "web", "instruccion": "buscar precio btc", "args": {"query": "precio bitcoin"}},
        {"tool": "file_write", "instruccion": "guardar en un archivo", "args": {"filename": "precio.txt"}},
    ]
    with _patch(gn, "_planner_llm", lambda o, m: plan):
        upd = node_planner({"orden": _ORDEN, "mem": {}})
    assert [p["tool"] for p in upd["plan_pasos"]] == ["web", "file_write"], upd["plan_pasos"]


def test_construir_orden_paso_propaga_filename():
    orden = _construir_orden_paso("guardar en un archivo", {"filename": "datos.txt"}, ["resultado previo"])
    assert "datos.txt" in orden


def test_node_file_write_usa_resultado_previo():
    fn = "precio_btc_test.txt"
    dest = os.path.join(str(CARPETA_AETHER), fn)
    try:
        out = node_file_write({
            "orden": f"guardar en {fn}",
            "mem": {},
            "plan_resultados": ["Precio de Bitcoin: $60,657.98 USD"],
        })
        assert os.path.exists(dest), "el archivo debe escribirse en ~/Aether"
        contenido = open(dest).read()
        assert "60,657.98" in contenido, contenido
        # node_file_write reporta la RUTA ABSOLUTA
        assert dest in out["final_response"], out["final_response"]
    finally:
        if os.path.exists(dest):
            os.remove(dest)


def test_flujo_executor_web_luego_filewrite():
    """e2e del executor: web (mock) → file_write (real) escribe el precio."""
    fn = "flujo_btc_test.txt"
    def fake_web(sub_state):
        return {"web_results": "Precio BTC", "final_response": "Precio de Bitcoin: $60,657.98 USD"}

    real_get = tool_registry.get_node_func
    def patched_get(tool):
        return fake_web if tool == "web" else real_get(tool)

    plan = [
        {"tool": "web", "instruccion": "buscar precio btc", "args": {}},
        {"tool": "file_write", "instruccion": "guardar en un archivo", "args": {"filename": fn}},
    ]
    dest = os.path.join(str(CARPETA_AETHER), fn)
    try:
        with _patch(tool_registry, "get_node_func", patched_get):
            estado = crear_estado_inicial(_ORDEN, {}, True)
            estado["plan_activo"] = True
            estado["plan_pasos"] = plan
            estado["plan_index"] = 0
            estado["plan_resultados"] = []
            # Paso 1: web
            out1 = node_plan_executor(estado)
            estado.update(out1)
            # Paso 2: file_write (real)
            out2 = node_plan_executor(estado)
        assert os.path.exists(dest), "file_write debe escribir el archivo en ~/Aether"
        contenido = open(dest).read()
        assert "60,657.98" in contenido, contenido
        # el contenido viene del resultado del paso web
        assert len(estado["plan_resultados"]) == 1  # tras paso 1
        assert "Guardado en" in (out2.get("final_response") or "")
    finally:
        if os.path.exists(dest):
            os.remove(dest)


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
