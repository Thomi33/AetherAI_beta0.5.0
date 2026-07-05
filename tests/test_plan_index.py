"""
Test aislado para confirmar la convención real de `plan_index` dentro del
`sub_estado` que arma `node_plan_executor`.

Cómo usarlo:
1. Copiá este archivo a la raíz de tu proyecto (mi_proyecto_crew) o ajustá
   el import de abajo para que apunte a tu `core.agent.graph_nodes`.
2. Corré: python test_plan_index.py
3. Mirá el output: te dice, para cada paso ejecutado, qué `plan_index`
   vio el nodo y qué `args` pudo (o no pudo) leer de `plan_pasos[idx]`.

No depende de LLM, shell real, ni red: monkeypatchea `get_node_func` para
usar nodos espía en vez de los reales.
"""

import sys

# ── Ajustá este path si hace falta ──────────────────────────────────────
sys.path.insert(0, "/home/thomi/mi_proyecto_crew")

from core.agent import graph_nodes as gn
from core.agent import tool_registry


# ══════════════════════════════════════════════════════════════════════
# Nodos espía: no hacen nada real, solo reportan qué índice/args ven
# ══════════════════════════════════════════════════════════════════════

def _espiar(nombre_tool):
    def nodo_espia(state):
        plan_index = state.get("plan_index")
        plan_pasos = state.get("plan_pasos") or []

        # Réplica EXACTA del patrón de _comando_explicito_del_paso (shell):
        # usa plan_index tal cual, sin offset.
        idx_shell_style = plan_index if isinstance(plan_index, int) and plan_index >= 0 else 0
        args_shell_style = (
            plan_pasos[idx_shell_style].get("args", {})
            if idx_shell_style < len(plan_pasos) and isinstance(plan_pasos[idx_shell_style], dict)
            else None
        )

        # Réplica EXACTA del patrón de node_file_write: idx = plan_index - 1
        idx_filewrite_style = max(0, (plan_index or 1) - 1)
        args_filewrite_style = (
            plan_pasos[idx_filewrite_style].get("args", {})
            if idx_filewrite_style < len(plan_pasos) and isinstance(plan_pasos[idx_filewrite_style], dict)
            else None
        )

        print(f"\n── Nodo espía para tool='{nombre_tool}' ──")
        print(f"   state['plan_index'] recibido = {plan_index}")
        print(f"   state['orden'] (armado por _construir_orden_paso) = {state.get('orden')!r}")
        print(f"   [estilo SHELL]     idx={idx_shell_style} -> args leídos = {args_shell_style}")
        print(f"   [estilo FILEWRITE] idx={idx_filewrite_style} -> args leídos = {args_filewrite_style}")

        # Marca cuál de los dos coincide con los args reales de ESTE paso
        args_reales_de_este_paso = plan_pasos[state.get("_paso_real_idx", -1)].get("args") if plan_pasos else None
        if args_shell_style == args_reales_de_este_paso:
            print("   ✅ El estilo SHELL apuntó a los args CORRECTOS de este paso.")
        if args_filewrite_style == args_reales_de_este_paso:
            print("   ✅ El estilo FILEWRITE apuntó a los args CORRECTOS de este paso.")
        if args_shell_style != args_reales_de_este_paso and args_filewrite_style != args_reales_de_este_paso:
            print("   ❌ NINGUNO de los dos estilos apuntó a los args correctos.")

        return {"final_response": f"[espía {nombre_tool} ejecutado]"}

    return nodo_espia


def main():
    # Plan de 2 pasos con args DISTINGUIBLES entre sí para no confundirnos
    plan_pasos = [
        {"tool": "web", "instruccion": "buscar precio de X", "args": {"query": "precio X", "marca_paso": "PASO_0_WEB"}},
        {"tool": "file_write", "instruccion": "guardar resultado", "args": {"filename": "precio_x.txt", "marca_paso": "PASO_1_FILEWRITE"}},
    ]

    # Monkeypatch: reemplazamos get_node_func para devolver nodos espía
    original_get_node_func = tool_registry.get_node_func

    def get_node_func_espia(tool):
        return _espiar(tool)

    tool_registry.get_node_func = get_node_func_espia
    gn.get_node_func = get_node_func_espia  # por si graph_nodes importó el símbolo directo

    # Import diferido para asegurar que el patch ya esté aplicado
    from core.agent.graph_state import crear_estado_inicial

    state = crear_estado_inicial(orden="tarea de prueba", mem={}, modo_autonomo=True)
    state["plan_activo"] = True
    state["plan_pasos"] = plan_pasos
    state["plan_index"] = 0
    state["plan_resultados"] = []

    print("=" * 70)
    print("SIMULANDO node_plan_executor paso a paso")
    print("=" * 70)

    for paso_real_idx in range(len(plan_pasos)):
        # Inyectamos una marca para que el nodo espía sepa cuál es el
        # índice REAL del paso que se está ejecutando en este loop
        state["_paso_real_idx"] = paso_real_idx
        resultado_update = gn.node_plan_executor(state)
        state.update(resultado_update)
        state["_paso_real_idx"] = paso_real_idx  # update pudo haberlo pisado

    tool_registry.get_node_func = original_get_node_func

    print("\n" + "=" * 70)
    print("CONCLUSIÓN: revisá arriba cuál estilo (SHELL o FILEWRITE) marcó")
    print("✅ en AMBOS pasos. Ese es el patrón que debe usar node_mcp.")
    print("=" * 70)


if __name__ == "__main__":
    main()
