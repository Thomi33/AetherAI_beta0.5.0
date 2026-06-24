#!/usr/bin/env python3
"""
Runner agregado de la suite de tests de Aether (Tool Planning unificado).

Ejecuta TODOS los módulos tests/test_*.py en subprocesos aislados y reporta
un resumen. Ninguno requiere Ollama: usan normalizar_mem/crear_estado_inicial
y mockean LLM/tools.

Uso:
    python test_planning.py
"""
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).parent
TESTS_DIR = RAIZ / "tests"


def main():
    modulos = sorted(TESTS_DIR.glob("test_*.py"))
    if not modulos:
        print("No se encontraron tests en tests/")
        return 1

    print("=" * 64)
    print("SUITE DE TESTS — Aether Tool Planning")
    print("=" * 64)

    fallidos = []
    for mod in modulos:
        print(f"\n▶ {mod.name}")
        print("-" * 64)
        res = subprocess.run(
            [sys.executable, str(mod)],
            capture_output=True,
            text=True,
        )
        # Mostrar la última línea de resumen de cada módulo
        lineas = [l for l in res.stdout.strip().splitlines() if l.strip()]
        resumen = lineas[-1] if lineas else "(sin salida)"
        # Mostrar líneas de fallo si las hay
        for l in lineas:
            if l.startswith("❌"):
                print("  " + l)
        print(f"  → {resumen}")
        if res.returncode != 0:
            fallidos.append(mod.name)
            if res.stderr.strip():
                print("  stderr:", res.stderr.strip().splitlines()[-1])

    print("\n" + "=" * 64)
    if fallidos:
        print(f"❌ FALLARON {len(fallidos)} módulo(s): {', '.join(fallidos)}")
        return 1
    print(f"✅ TODOS los {len(modulos)} módulos de test pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
