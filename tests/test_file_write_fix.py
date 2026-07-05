"""
Test de regresión para el fix de indexado en node_file_write.

Antes del fix: en un plan [web, file_write], node_file_write leía los
args del paso 0 (web) en vez de los suyos propios -> el `filename`
explícito se ignoraba silenciosamente.

Este test arma ese plan exacto, usa el node_file_write REAL (no un
espía), y verifica que el archivo se escriba con el nombre pedido.

Uso:
    cp test_file_write_fix.py /home/thomi/mi_proyecto_crew/
    cd /home/thomi/mi_proyecto_crew
    python test_file_write_fix.py
"""

import os
import sys
import tempfile

sys.path.insert(0, "/home/thomi/mi_proyecto_crew")

from core.agent import graph_nodes as gn
from core.agent import tool_registry
from core.agent.graph_state import crear_estado_inicial


def nodo_web_espia(state):
    # No pegamos a la red: devolvemos datos crudos fijos, como si "web" ya
    # hubiese corrido.
    return {"web_results": "precio de X: 123 USD (dato simulado)"}


def main():
    tmpdir = tempfile.mkdtemp(prefix="aether_test_filewrite_")
    nombre_esperado = "precio_x_TEST.txt"
    ruta_esperada = os.path.join(tmpdir, nombre_esperado)

    plan_pasos = [
        {"tool": "web", "instruccion": "buscar precio de X", "args": {"query": "precio X"}},
        {"tool": "file_write", "instruccion": "guardar resultado",
         "args": {"filename": ruta_esperada}},
    ]

    original_get_node_func = tool_registry.get_node_func

    def get_node_func_mixto(tool):
        if tool == "web":
            return nodo_web_espia
        return original_get_node_func(tool)  # file_write REAL (con el fix)

    tool_registry.get_node_func = get_node_func_mixto
    gn.get_node_func = get_node_func_mixto

    state = crear_estado_inicial(orden="tarea de prueba", mem={}, modo_autonomo=True)
    state["plan_activo"] = True
    state["plan_pasos"] = plan_pasos
    state["plan_index"] = 0
    state["plan_resultados"] = []

    print("=" * 70)
    print(f"Directorio temporal: {tmpdir}")
    print(f"Filename esperado (paso 2, args propios): {ruta_esperada}")
    print("=" * 70)

    for _ in range(len(plan_pasos)):
        resultado_update = gn.node_plan_executor(state)
        state.update(resultado_update)

    tool_registry.get_node_func = original_get_node_func

    print("\nfinal_response reportado:", state.get("final_response"))
    print("error_activo:", state.get("error_activo"), "-", state.get("error_mensaje"))

    if os.path.exists(ruta_esperada):
        with open(ruta_esperada) as f:
            contenido = f.read()
        print(f"\n✅ ÉXITO: el archivo se creó en la ruta pedida por args del PASO 2.")
        print(f"   Contenido: {contenido!r}")
    else:
        print(f"\n❌ FALLO: no se encontró {ruta_esperada}.")
        print(f"   Archivos en {tmpdir}: {os.listdir(tmpdir)}")
        print("   (Si aparece un archivo con otro nombre ahí, el bug sigue activo:")
        print("    filename se está leyendo del paso equivocado.)")


if __name__ == "__main__":
    main()
