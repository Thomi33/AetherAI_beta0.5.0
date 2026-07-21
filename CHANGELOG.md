# Changelog

## [1.0.0] - 2026-07-11
### Added
- Migración completa de CrewAI a LangGraph puro.
- Nueva arquitectura de nodos del grafo (`node_planner`, `node_plan_executor`, etc.).
- Subsistema de memoria persistente en SQLite con esquema versionado (`core/memory/store/`).
- Ejecución robusta de comandos shell con barreras de seguridad y timeout.
- TUI moderna con Textual (`run.py`).

### Changed
- Actualización de configuración: el modelo por defecto ahora es `ornith:9b`.
- Cambio de ruta de base de datos de producción a `/mnt/nvme/Aether/db/current.db`.
- Optimizaciones de latencia de Ollama (`OLLAMA_KEEP_ALIVE=-1`).

### Removed
- Dependencias de CrewAI eliminadas.
- Tests obsoletos que dependían de funciones eliminadas en la refactorización.
- Scripts y documentación legacy (`jarvis.py.old`, `manual_arch.md`).

## [0.5.2026] - 2026-06-28
### Added
- Mejoras en planner
- Ajustes en routing de intents

### Fixed
- Corrección de bug en gitignore merge conflict