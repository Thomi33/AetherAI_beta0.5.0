#!/usr/bin/env python3
"""
Tests del CONTEXTO CONVERSACIONAL presupuestado (más turnos, sin desbordar).

Verifica:
- Se inyectan MÁS de 50 turnos (antes era tope fijo de 50).
- El presupuesto de caracteres acota el bloque y conserva los turnos MÁS
  RECIENTES (descarta los más viejos).
- Respeta el tope MAX_TURNOS_CONTEXTO.
- No crashea con turnos malformados.

Ejecutar: python tests/test_context_budget.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.memory.context_builder as cb


class _patch:
    def __init__(self, obj, attr, valor):
        self.obj, self.attr, self.valor = obj, attr, valor
    def __enter__(self):
        self.orig = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.valor)
        return self
    def __exit__(self, *a):
        setattr(self.obj, self.attr, self.orig)


def _mem(n_turnos, texto="hola"):
    return {
        "preferencias": {"nombre_usuario": "Thomas", "navegador": "brave", "notas": []},
        "flatpaks": {}, "historial_comandos": [],
        "conversacion": [
            {"rol": "usuario" if i % 2 == 0 else "jarvis",
             "texto": f"{texto}-{i}", "fecha": "2026-06-24 10:00"}
            for i in range(n_turnos)
        ],
    }


def _contar_turnos(texto_contexto):
    # cuenta líneas de turno en el bloque de conversación
    return sum(1 for ln in texto_contexto.splitlines()
               if ln.strip().startswith(("[USUARIO]:", "[JARVIS]:")))


def test_incluye_mas_de_50_turnos():
    # Sin recuerdos de DB para test hermético
    with _patch(cb, "obtener_recuerdos", lambda **k: []):
        # 120 turnos cortos, presupuesto amplio → deben entrar los 120
        with _patch(cb, "MAX_TURNOS_CONTEXTO", 200), _patch(cb, "CONTEXTO_CONV_MAX_CHARS", 100000):
            ctx = cb.construir_contexto_memoria(_mem(120))
    assert _contar_turnos(ctx) == 120, _contar_turnos(ctx)
    assert "120 turnos" in ctx


def test_respeta_presupuesto_y_conserva_recientes():
    with _patch(cb, "obtener_recuerdos", lambda **k: []):
        # presupuesto chico → se recortan los viejos, se conservan los nuevos
        with _patch(cb, "MAX_TURNOS_CONTEXTO", 200), _patch(cb, "CONTEXTO_CONV_MAX_CHARS", 200):
            mem = _mem(100, texto="mensaje")
            ctx = cb.construir_contexto_memoria(mem)
    # El turno más reciente (índice 99) debe estar; el más viejo (0) no.
    assert "mensaje-99" in ctx
    assert "mensaje-0\n" not in ctx and "mensaje-0]" not in ctx
    # No debería incluir los 100 (presupuesto lo impide)
    assert _contar_turnos(ctx) < 100


def test_respeta_tope_max_turnos():
    with _patch(cb, "obtener_recuerdos", lambda **k: []):
        with _patch(cb, "MAX_TURNOS_CONTEXTO", 10), _patch(cb, "CONTEXTO_CONV_MAX_CHARS", 100000):
            ctx = cb.construir_contexto_memoria(_mem(50))
    # Aunque hay 50 turnos, el tope es 10
    assert _contar_turnos(ctx) == 10


def test_no_crashea_con_turnos_malformados():
    with _patch(cb, "obtener_recuerdos", lambda **k: []):
        mem = {"preferencias": {"nombre_usuario": "X", "navegador": "b", "notas": []},
               "flatpaks": {}, "historial_comandos": [],
               "conversacion": [{}, {"rol": "usuario"}, {"texto": "solo texto"}]}
        ctx = cb.construir_contexto_memoria(mem)  # no debe lanzar
    assert isinstance(ctx, str)


def test_sin_turnos_no_incluye_bloque():
    with _patch(cb, "obtener_recuerdos", lambda **k: []):
        ctx = cb.construir_contexto_memoria(_mem(0))
    assert "CONTEXTO DE SESIONES ANTERIORES" not in ctx


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
