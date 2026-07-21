#!/usr/bin/env python3
"""Regresiones del loop de control visual, sin pantalla ni ydotool reales."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.agent.graph_nodes as gn


class _patch:
    def __init__(self, obj, attr, value):
        self.obj, self.attr, self.value = obj, attr, value

    def __enter__(self):
        self.original = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.value)
        return self

    def __exit__(self, *args):
        setattr(self.obj, self.attr, self.original)


def test_accion_click_valida_y_acotada():
    assert gn._parsear_accion_computer_use('{"accion":"click","x":10,"y":20}') == {
        "accion": "click", "x": 10, "y": 20,
    }
    assert gn._parsear_accion_computer_use('{"accion":"click","x":-1,"y":20}') is None
    assert gn._parsear_accion_computer_use('{"accion":"click","x":999999,"y":20}') is None


def test_planner_detecta_control_de_gui_sin_llm():
    update = gn.node_planner({"orden": "hacé click en el botón guardar", "mem": {}})
    assert update["plan_pasos"][0]["tool"] == "computer_use"


def test_loop_ejecuta_acciones_y_se_detiene_al_completar():
    respuestas = iter([
        '{"accion":"click","x":100,"y":200}',
        '{"accion":"escribir","texto":"hola"}',
        '{"accion":"listo"}',
    ])
    acciones = []

    with _patch(gn, "ver_pantalla", lambda pregunta: next(respuestas)), \
         _patch(gn, "click_en", lambda x, y: (acciones.append(("click", x, y)) or ("OK", False))), \
         _patch(gn, "escribir_texto", lambda texto: (acciones.append(("escribir", texto)) or ("OK", False))):
        update = gn.node_computer_use({"orden": "completá el formulario"})

    assert acciones == [("click", 100, 200), ("escribir", "hola")]
    assert "Objetivo cumplido" in update["computer_use_result"]
    assert not update.get("error_activo")


def test_error_de_vision_se_proporciona_al_error_handler():
    with _patch(gn, "ver_pantalla", lambda pregunta: "No pude capturar la pantalla, compa."):
        update = gn.node_computer_use({"orden": "hacé click en guardar"})

    assert update["error_activo"] is True
    assert update["error_contexto"] == "computer_use"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"✅ {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"❌ {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests OK")
    raise SystemExit(1 if failures else 0)
