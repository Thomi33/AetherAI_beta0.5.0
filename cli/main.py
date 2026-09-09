"""
Aether CLI (deprecated)

El camino recomendado es el lanzador global:

    aether                 # TUI moderna (equivale a: cd <proyecto> && python run.py)
    aether task "ORDEN"    # one-shot sobre el grafo
    aether --workdir RUTA  # operar sobre otra carpeta (default: el $PWD actual)
    aether doctor          # diagnóstico
    aether --help

Para instalarlo, ver README → "Opción 0 — Lanzador global `aether`",
o directamente:

    ln -sf /home/thomi/mi_proyecto_crew/bin/aether ~/.local/bin/aether

Este archivo se mantiene solo por compatibilidad. Usa el TUI (run.py) o
bin/aether.
"""
