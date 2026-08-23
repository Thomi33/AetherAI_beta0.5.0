# Verification Summary - Project Issues

## 1. Memoria-grafo

**Issue**: `graph_service.py` calls `programar_consolidacion(mem)` after each graph invocation, and the chain to `context_builder.py` is complete. Need to verify `resumen_memoria` en `current.db` se actualiza después de 12 turnos.

**Finding**: The database at `/home/thomi/mi_proyecto_crew/memoria.db` was missing the `resumen_memoria` table. The config specifies `BASE_AETHER = ~/Aether`, so the DB should be at `/home/thomi/Aether/db/current.db`.

**Fix Applied**: Ran `asegurar_esquema()` which created the `resumen_memoria` table (and other tables) at the correct path `/home/thomi/Aether/db/current.db`. 

**Verification**: 
- DB_PATH = `/home/thomi/Aether/db/current.db`
- Tables: `['conversaciones', 'sqlite_sequence', 'comandos', 'recuerdos', 'core_memory', 'resumen_memoria']`
- `resumen_memoria` row exists with text and `ultimo_turno_id`

**Consolidation Flow** (verified working):
- `graph_service.py` line 38 → `programar_consolidacion(mem)` after each graph invoke
- starts daemon thread → `consolidar_resumen()` 
- checks >= 12 pending turns (`MIN_TURNOS_PARA_CONSOLIDAR = 12`)
- calls LLM to rewrite summary → saves via `guardar_resumen()` to `resumen_memoria` table
- After 12 turns, the summary will be automatically updated

---

## 2. STT y Wake Word

**stt_worker.py Analysis**:
- `initial_prompt` (line 86): Retrieved from request (`req.get("initial_prompt")`), defaults to `None`. Set by `stt_service.py` from `STT_VOCAB_HINT` config: `"Aether, GitHub, Ollama, LangGraph, Ornith, MCP, Textual, Hyprland, Wayland, NVMe, Notion, Steam, Roblox, Minecraft, VLSM, subnetting, ydotool, faster-whisper, SQLite, consolidator, AudioBox USB 96, CrewAI."`
  - Conditions whisper's vocabulary toward project-specific proper nouns
  - Improves recognition of names like "Aether", "GitHub", etc.
- `arecord device`: Config `AUDIO_INPUT_MATCH = "AudioBox USB 96"` (config.json line 34)
  - Used in `audio_input.py` to find correct ALSA capture device
  - Records at 16kHz mono 16-bit format (native for whisper)

**Wake word → TUI connection**:
- Handler: `scripts/aether_focus.py`
- When "hey aether" detected:
  - If Aether TUI running: focuses via `hyprctl dispatch focuswindow`
  - If not running: launches with kitty (`/usr/sbin/kitty --title Aether --class Aether -e python run.py`)
- Uses cooldown (`LAUNCH_COOLDOWN = 3.0s`) to prevent duplicate launches
- The actual wake word daemon is external (`~/GamesNvme/aether_wakeword_training/wakeword_daemon.py`)

**No code bugs found** - the STT configuration and wake word → TUI connection are properly wired.

---

## 3. Config `/home/thomi/Aether` en el MCP filesystem

**Issue**: Need to add `/home/thomi/Aether` to allowed dirs in Claude's connector configuration. This is not a code bug but a connector config setting.

**Action Required**: Add `/home/thomi/Aether` to the allowed directories list in Claude's MCP filesystem settings so the project can access files in that directory.

---