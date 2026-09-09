Aether
Summary
Aether — personal local AI agent (LangGraph + Ollama); read for architecture, subsystems, and open work

Details
Personal AI agent built on LangGraph (migrated from CrewAI), running locally via Ollama
Long-term vision: a proactive, always-present local desktop AI ("un Jarvis en silencio") that anticipates needs, controls the desktop, and acts without being explicitly prompted
Codebase lives at /home/thomi/mi_proyecto_crew
Stack: LangGraph + Ollama + a Textual-based TUI, with Filesystem MCP access to /home/thomi/mi_proyecto_crew and /home/thomi/GamesNvme/aether_wakeword_training
Recent work

Structured codebase audit and live remediation: confirmed false positives from a Cline-generated audit, fixed a context_builder.py data-loss logging gap, archived dead code (error_handler.py)
Fixed core behavioral bug where Aether never executed multi-step agentic plans due to a narrow _parece_multitool() detection function
Live test task: asked Aether to create /home/thomi/GamesNvme/temp_converter/ and write a Python temperature-converter console app inside it; Aether used shell heredocs (via core/tools/shell_executor.py) instead of the dedicated core/tools/file_writer.py tool, and the write silently failed — folder stayed empty, then python3 temp_converter.py errored with file-not-found
Root cause found and fixed: shell_executor.py's _LANZADORES_GUI regex misclassified any "python3 <file>.py" or "./..." command as a GUI app launch, running it backgrounded with stdout to DEVNULL and no wait for real completion — broke both the heredoc file-write and any synchronous script test run; narrowed the regex to only match named GUI apps
Deeper architectural gap found (why Aether reached for shell heredocs in the first place): neither of the two file-writing tools in the registry could originally do "create a folder + write named files at a specific path". node_codigo was limited to a single scratch file and node_file_write/file_writer.escribir_archivo inferred its destination from free-form text.

FIXED in the current graph: node_codigo resolves an explicit destination and persists the generated source; the native agent_loop and filesystem_tool.py add structured fs_write, fs_read, fs_mkdir, and fs_list operations. fs_write accepts one file or an atomic best-effort batch, so multi-file projects no longer depend on heredocs or regex-inferred paths. The legacy file_write fast-path remains for compatibility.

Separately observed: after the file-not-found error (which included a pyenv path ".../versions/3.12.7/bin/python3"), Aether's launch/app-opening flow got confused and tried to interpret "3.12.7" as an app name to launch (checked flatpak, then which 3.12.7) — cause not yet diagnosed, flagged as a possible new routing bug to investigate
Routing and intent detection

Multiple sessions fixing keyword-based routing bugs
_parece_multitool() rewrite for multi-step plan detection
_parece_correccion_usuario() deterministic pre-classifier to intercept user correction messages
Fixed overly generic web-intent keywords (cuánto, hoy, actualmente) causing local system queries to route to web search
Fixed computer_use regex truncating at underscores
Fixed launch keyword collisions with UI control phrases
Memory system

Replaced raw chat injection with a rolling resumen_memoria summary stored in SQLite, compacted every 12 turns via consolidator.py
Added /memory TUI command with editable textarea
Confirmed core/memory/store/ parallel subsystem is not wired into the active graph
STT integration

Implemented faster-whisper push-to-talk (F2) via a two-process architecture: stt_worker.py in an isolated venv (~/whisper_aether_test/venv-stt) communicating over stdin/stdout JSON-lines, and stt_service.py in the main venv
Real-world transcription accuracy noticeably worse than isolated tests; initial_prompt and audio device selection (arecord vs AudioBox USB 96) identified as likely causes, left pending
Earlier STT groundwork: WhisperSTT class tested in isolated venv (~/whisper_aether_test/), medium model + CPU int8 chosen to avoid VRAM contention; HF_HUB_CACHE path conflict with a removed HDD resolved
Wake word system

Custom "hey aether" openWakeWord model trained at /home/thomi/GamesNvme/aether_wakeword_training/
Second training pass (n_samples 80k, steps 60k, real voice clips) achieved ~65–70% detection at threshold 0.3 with zero false positives
Temporal post-filter added (--consecutive 3, --window 0.75s)
Daemon runs as a systemd user service
Detections were logging only; connecting to a real action (opening/focusing the TUI) was a stated next step
Computer use / desktop control

Implemented computer_control.py using ydotool + grim for Wayland-compatible mouse/keyboard injection and screen capture
Fixed silent routing bugs in node_plan_executor and _CAMPOS_RESULTADO_POR_TOOL
mover_mouse action was missing from the VLM prompt, parser, and dispatch branch — all fixed
MCP integration

Single internal "mcp" tool with routing via {server, name, arguments}
Persistent connections via daemon thread
GitHub query normalization via _normalizar_args_mcp
Fixed mcp_client.py silently discarding input_schema from all tools
Two-layer fix for keyword-triggered MCP intent firing without action verbs
TUI redesign

Migrated from plain CLI to Textual framework with Claude Code–style aesthetics
Added modal selector screens (ModelSelectorScreen, EffortSelectorScreen, McpSelectorScreen, etc.) and slash command autocomplete
Effort system maps temperature + num_predict per level
Fixed paste bug: Textual's base Input widget truncated multi-line pastes to the first line only (event.text.splitlines()[0]); replaced with a custom PasteInput widget (tui/widgets/paste_input.py) that collapses long/multiline pastes to a "[pasted N characters]" placeholder and resolves it to the real text before sending to the engine
Wants the collapsed paste placeholder to be expandable to view the original pasted content (added Ctrl+R -> PastePreviewScreen modal in selector_screens.py)
Confirmed: the underlying full pasted text (not the placeholder) is what should reach the prompt sent to the model/graph
Wants language options added (e.g. English and Spanish) — still pending, scope (UI strings only vs. Aether's responses too) not yet clarified
Model evaluation

Using Ornith 1.5:9b for the temp_converter test task; trusts the Ornith model family given strong results in other tests
Tested Ornith-9B (current main model)
Evaluated Ornith-35B (VRAM issues on RTX 3060 — ~10 layers fit in VRAM)
Explored Gemma 4 26B-A4B MoE as a candidate
Qwen3-Coder-30B-A3B tested for routing behavior analysis
Planner fixes

Fixed num_predict scaling bug causing JSON truncation in planner outputs; NUM_PREDICT_PLANNER key added
Fixed session isolation bug: obtener_turnos_por_tema was querying without sesion_id filter, contaminating context across sessions
History

Fully migrated from CrewAI to LangGraph (confirmed via grep, tests 113/113); legacy core/services/aether_service.py renamed and confirmed dead
Initial architecture audit identified dead node_router, mcp_servers.json gitignore gap (no commit had been made with tokens), silent ImportError in _planner_llm, GitHub-specific logic hardcoded in planner
New core/connectors/ package created with auto-discovery registry
NVMe migration: data directory moved from /mnt/basurero/Aether to /mnt/nvme/Aether (ext4, 119.2GB); single BASE_AETHER constant in settings.py governs all derived paths
Design philosophy: Aether should achieve the user's objective regardless of the specific method/tool used to get there (goal-oriented over method-fixed)
Currently thinking about how to improve tool routing, given history of keyword-based routing bugs
Clarified goal: deterministic routing should not discard tools before the model can reason about them; open-ended work should use ReAct-style tool calling
Implemented node_agent_loop with Ollama-native tool calling and structured schemas from TOOL_REGISTRY
The loop executes tool calls, feeds results back to the model, and ends when the model returns without more tool calls; deterministic planner/executor remains for fast-paths
Added the unified filesystem tools fs_write, fs_read, fs_mkdir, and fs_list; fs_write supports atomic best-effort multi-file batches
Added the skills/ catalog: reusable SKILL.md instructions are discovered per order and loaded on demand through fs_read
Tool registration now includes native tools and MCP under the same registry/schema path
The loop currently has no product-level hard iteration cap; failures still pass through the existing error handler
Remaining work: validate long-running agent-loop behavior and decide how retries should interact with user confirmation
