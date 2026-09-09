# Changelog

## [Unreleased]
### Added
- **Skills** (`core/skills/registry.py` + carpeta `skills/`): instrucciones reutilizables para tareas recurrentes, en `skills/<nombre>/SKILL.md` (frontmatter `name`/`description` + contenido libre). El catálogo (nombre+descripción+ruta) se inyecta automáticamente en el system prompt (`construir_backstory`); el modelo decide solo si conviene leer alguna completa con la nueva tool `fs_read` antes de actuar. Agregar una skill nueva no requiere tocar código ni reiniciar (también puede pedírselo al propio Aether mediante `fs_write`). Primera skill incluida: `crear-proyecto-python`.
- Dictado por voz en la TUI (F2, push-to-talk): `core/services/stt_worker.py` corre faster-whisper (modelo `medium`, CPU/int8) como proceso persistente y aislado en `~/whisper_aether_test/venv-stt`; `core/services/stt_service.py` habla con él por stdin/stdout (JSON-lines) y graba audio con `arecord` (sin dependencias nuevas en el venv principal). Config nueva: `STT_ENABLED`, `STT_LANGUAGE`, `STT_VENV_PYTHON`.
- **Runtime CLI global**: `bin/aether` (bash launcher auto-localizable) + `bin/aether_run.py`. Permite lanzar Aether desde **cualquier directorio** con un comando `aether` global (symlink en `~/.local/bin`): TUI por defecto, `task "..."` (one-shot sobre el grafo, ideal para scripts/cron), `cli`, `doctor` (diagnóstico) y `--version`.
- **Directorio de trabajo**: Aether opera sobre el directorio desde donde lo invocás (`~/Documents`, `~/Proyecto`, …), no encerrado en `~/Aether`. `bin/aether` captura `$PWD` en `AETHER_CWD` antes de su `cd` interno; `settings.RUTA_TRABAJO` (lazy) lo expone; `filesystem_tool`/`file_writer` resuelven relativas ahí; `shell_executor` corre con `cwd=RUTA_TRABAJO`; el system prompt incluye `[DIRECTORIO DE TRABAJO]`. Flag `aether --workdir <ruta>` (también en `task`, `run.py`, `jarvis_new.py`). Autorización explícita una vez por directorio en `~/.aether/allowed_dirs.json` (`core/config/dir_authorization.py`); banner en TUI + chequeo en `doctor`.

### Fixed
- `jarvis_new.py` (`aether cli`): ahora trata `EOFError` como fin de sesión y termina limpio (antes entraba en loop infinito de error con stdin cerrado).

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