#!/usr/bin/env python3
"""Ejecuta los módulos ``tests/test_*.py`` en procesos aislados.

Uso: ``python test_planning.py`` desde la raíz del proyecto.
"""
import subprocess
import sys
from pathlib import Path


RAIZ = Path(__file__).parent
TESTS_DIR = RAIZ / "tests"


def main() -> int:
    modulos = sorted(TESTS_DIR.glob("test_*.py"))
    if not modulos:
        print("No se encontraron tests en tests/")
        return 1

    print("=" * 64)
    print("SUITE DE TESTS — Aether")
    print("=" * 64)

    fallidos: list[str] = []
    for mod in modulos:
        print(f"\n▶ {mod.name}")
        res = subprocess.run([sys.executable, str(mod)], text=True, capture_output=True)
        lineas = [linea for linea in res.stdout.splitlines() if linea.strip()]
        if res.returncode == 0:
            print(f"  → {lineas[-1] if lineas else 'OK'}")
            continue

        fallidos.append(mod.name)
        print(f"  → {lineas[-1] if lineas else 'sin salida'}")
        if res.stderr.strip():
            print(f"  stderr: {res.stderr.strip().splitlines()[-1]}")

    print("\n" + "=" * 64)
    if fallidos:
        print(f"❌ FALLARON {len(fallidos)} módulo(s): {', '.join(fallidos)}")
        return 1
    print(f"✅ TODOS los {len(modulos)} módulos de test pasaron")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
