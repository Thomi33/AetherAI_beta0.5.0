#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/thomi/mi_proyecto_crew')

from core.agent.tool_registry import TOOL_REGISTRY, validar_plan
from core.agent.graph_builder import build_graph, _TOOLS_CON_RESUMEN
from core.memory.consolidator import MIN_TURNOS_PARA_CONSOLIDAR
from core.tools.shell_executor import _PATRONES_PELIGROSOS, EDITORES_BANEADOS, _LANZADORES_GUI
from core.config.settings import CONTEXTO_CONV_MAX_CHARS, MAX_TURNOS_CONTEXTO, MAX_TURNOS_CONTEXTO_PLAN, MAX_TURNOS_CONTEXTO_CHAT
from core.connectors.filesystem import CONNECTOR as fs_connector
from core.connectors.github import CONNECTOR as github_connector

print("AETHER PROJECT INTEGRATION AUDIT")
print("="*60)

print(f"\n1. TOOL REGISTRY: {len(TOOL_REGISTRY)} tools: {sorted(TOOL_REGISTRY.keys())}")

print(f"\n2. _TOOLS_CON_RESUMEN: {sorted(_TOOLS_CON_RESUMEN)}")
all_tools = set(TOOL_REGISTRY.keys())
not_in_resumen = all_tools - _TOOLS_CON_RESUMEN
print(f"   Not in resumen: {sorted(not_in_resumen)}")

print("\n3. VALIDATION:")
tests = [
    ([{'tool': 'text', 'instruccion': 'saluda'}], 'Valid simple'),
    ([{'tool': 'inexistente', 'instruccion': 'x'}], 'Invalid tool'),
    ([{'instruccion': 'x'}], 'Missing tool'),
    ([], 'Empty plan'),
    ([{'tool': 'web'}], 'Missing instruction'),
    ([{'tool': 'vision'}], 'Vision no req.instr'),
    ([{'tool': 'web', 'args': {'query': 'algo'}}], 'Instr in args'),
    ([{'tool': 'text', 'instruccion': 'x', 'args': 'no-dict'}], 'Args not dict'),
]
for plan, desc in tests:
    ok, err = validar_plan(plan)
    print(f"   {desc}: ok={ok}")

print("\n4. GRAPH BUILD:", "OK" if build_graph() else "FAILED")

print(f"\n5. MIN_TURNOS_PARA_CONSOLIDAR: {MIN_TURNOS_PARA_CONSOLIDAR}")

print("\n6. SHELL SAFETY:")
print(f"   Dangerous patterns: {len(_PATRONES_PELIGROSOS)}")
print(f"   Banned editors: {EDITORES_BANEADOS}")
print(f"   GUI regex: {_LANZADORES_GUI.pattern[:60]}...")

print("\n7. CONTEXT BUDGET:")
print(f"   CONTEXTO_CONV_MAX_CHARS: {CONTEXTO_CONV_MAX_CHARS}")
print(f"   MAX_TURNOS_CONTEXTO: {MAX_TURNOS_CONTEXTO}")
print(f"   MAX_TURNOS_CONTEXTO_PLAN: {MAX_TURNOS_CONTEXTO_PLAN}")
print(f"   MAX_TURNOS_CONTEXTO_CHAT: {MAX_TURNOS_CONTEXTO_CHAT}")

fs_perm = fs_connector.permissions.get('write_file', 'not set')
github_issue_perm = github_connector.permissions.get('create_issue', 'not set')
print("\n8. MCP PERMISSIONS:")
print(f"   Filesystem write_file: {fs_perm}")
print(f"   GitHub create_issue: {github_issue_perm}")
print(f"   GitHub default: {github_connector.default_permission}")