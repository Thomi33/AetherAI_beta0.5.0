#!/usr/bin/env python3
"""
Empirical validation battery for Ornith adaptation in Aether.

Compares ORNITH_NATIVE_ADAPTATION=True (current) vs False (legacy).

Metrics covered:
- Prompt size (chars as proxy for tokens)
- Format compliance & extraction success (shell, code)
- Reasoning separation quality (pollution rate)
- Error recovery / robustness
- Multi-turn / long context simulation
- Number of LLM calls in flows (via mocks)
- Overhead of new parsing

Run: python tests/test_ornith_empirical.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
import core.agent.graph_nodes as gn
from core.config import settings
from core.agent.graph_nodes import (
    _llm_chat,
    _parse_ornith_thinking,
    extraer_comando_shell,
    _plan_activado,
    node_planner,
)
from core.memory.memory_manager import normalizar_mem
from core.agent.graph_state import crear_estado_inicial


# ──────────────────────────────────────────────────────────────────────
# TEST DATA: Simulated Ornith outputs (realistic based on official format)
# ──────────────────────────────────────────────────────────────────────

SIMPLE_THINK_SHELL = """<think>
The user wants to clean pip cache. The standard command on most systems including Arch is `pip cache purge`. I should use the exact format required.
</think>

To clean the pip cache, run:
[SHELL]pip cache purge[/SHELL]"""

THINK_WITH_CODE = """<think>
I need to write a small Python function. Let's think about edge cases: negative numbers, 0, 1, primes.
</think>

```python
def is_prime(n):
    if n <= 1:
        return False
    for i in range(2, int(n**0.5)+1):
        if n % i == 0:
            return False
    return True
```"""

NO_THINK_GOOD_SHELL = """Please execute this:
[SHELL]ls -la /tmp[/SHELL]"""

BAD_MIXED = """<think>I am thinking about what to do.
The command should be ls
</think>
Some prose here [SHELL]ls -l[/SHELL] and more text"""

TOOL_XML_STYLE = """<think>
The user asked for weather. I should call the tool.
</think>

<tool_call>
<function=get_weather>
<parameter=city>
Paris
</parameter>
</function>
</tool_call>"""

LONG_HISTORY_SIM = "Previous context " * 200   # simulate large injected context


# ──────────────────────────────────────────────────────────────────────
# HELPER: Capture what is sent to LLM
# ──────────────────────────────────────────────────────────────────────

class Capture:
    def __init__(self):
        self.calls = []
        self.last_prompt_len = 0

    def fake_llm(self, system=None, user=None, messages=None, on_token=None, tools=None):
        if messages:
            content = " ".join(str(m.get("content", "")) for m in messages)
        else:
            content = (system or "") + (user or "")
        self.last_prompt_len = len(content)
        self.calls.append({
            "messages": messages,
            "len": len(content),
            "tools": bool(tools),
        })
        # Return a realistic Ornith response
        return SIMPLE_THINK_SHELL


def make_mem_with_history(n_turns=20):
    mem = normalizar_mem({})
    for i in range(n_turns):
        mem["conversacion"].append({
            "rol": "user" if i % 2 == 0 else "jarvis",
            "texto": f"Turn {i}: do something with pip and files.",
            "sesion_id": "s1",
            "tema": "shell" if i % 3 == 0 else "text",
        })
    return mem


# ──────────────────────────────────────────────────────────────────────
# METRIC COLLECTORS
# ──────────────────────────────────────────────────────────────────────

def measure_extraction(raw_response, use_native):
    if use_native:
        _, cleaned = _parse_ornith_thinking(raw_response)
    else:
        cleaned = raw_response
    cmd = extraer_comando_shell(cleaned)
    success = bool(cmd)
    pollution = "<think>" in cleaned and success  # think leaked into extracted?
    return {
        "extraction_success": success,
        "extracted_cmd": cmd,
        "pollution": pollution,
        "cleaned_len": len(cleaned),
    }


def measure_parse_quality(raw):
    reasoning, answer = _parse_ornith_thinking(raw)
    return {
        "has_reasoning": bool(reasoning),
        "reasoning_len": len(reasoning),
        "answer_len": len(answer),
        "think_removed": "<think>" not in answer and "</think>" not in answer,
    }


# ──────────────────────────────────────────────────────────────────────
# MAIN TEST BATTERY
# ──────────────────────────────────────────────────────────────────────

def run_battery():
    results = {}
    orig_flag = settings.ORNITH_NATIVE_ADAPTATION

    capture = Capture()
    orig_llm = gn._llm_chat
    gn._llm_chat = capture.fake_llm

    try:
        for mode, flag in [("legacy", False), ("native", True)]:
            settings.ORNITH_NATIVE_ADAPTATION = flag
            use_native = flag

            mode_results = {
                "prompt_sizes": [],
                "extraction_success_rate": 0,
                "pollution_rate": 0,
                "parse_quality": [],
                "multi_step_calls": 0,
                "long_context_prompt_len": 0,
                "error_recovery": 0,
                "format_compliance": 0,
            }

            cases = [
                ("simple_shell", SIMPLE_THINK_SHELL),
                ("code_block", THINK_WITH_CODE),
                ("no_think", NO_THINK_GOOD_SHELL),
                ("mixed_bad", BAD_MIXED),
                ("tool_xml", TOOL_XML_STYLE),
            ]

            successes = 0
            pollutions = 0
            for name, resp in cases:
                ext = measure_extraction(resp, use_native)
                if ext["extraction_success"]:
                    successes += 1
                if ext.get("pollution"):
                    pollutions += 1

                pq = measure_parse_quality(resp)
                mode_results["parse_quality"].append(pq)

            mode_results["extraction_success_rate"] = successes / len(cases)
            mode_results["pollution_rate"] = pollutions / len(cases)

            # Prompt size via planner / _llm_chat path (uses current _llm_chat)
            mem = make_mem_with_history(5)
            estado = crear_estado_inicial("limpia la cache de pip por favor", mem)
            estado["sesion_id"] = "s1"

            # Force a planner path that calls _llm_chat internally (multi or fallback)
            # We capture size inside fake_llm
            try:
                node_planner(estado)
            except Exception:
                pass  # ignore routing for size capture

            mode_results["prompt_sizes"].append(capture.last_prompt_len)

            # Long context simulation
            long_mem = make_mem_with_history(80)
            long_estado = crear_estado_inicial("busca info y luego ejecuta algo", long_mem)
            try:
                node_planner(long_estado)
            except Exception:
                pass
            mode_results["long_context_prompt_len"] = capture.last_prompt_len

            # Multi-tool / planning robustness (count internal calls)
            capture.calls = []
            try:
                # This path often triggers planner LLM
                node_planner(crear_estado_inicial(
                    "busca el precio del dolar y guardalo en archivo", make_mem_with_history(3)
                ))
            except Exception:
                pass
            mode_results["multi_step_calls"] = len(capture.calls)

            # Error recovery: bad output
            recovery = 0
            for bad in [BAD_MIXED, "random garbage without format", "<think>only thinking</think>"]:
                ext = measure_extraction(bad, use_native)
                if not ext["extraction_success"]:
                    # Good if it doesn't crash and we can fall back
                    recovery += 1
            mode_results["error_recovery"] = recovery / 3.0

            # Format compliance proxy
            fmt_ok = 0
            for r in [SIMPLE_THINK_SHELL, NO_THINK_GOOD_SHELL]:
                if "[SHELL]" in (r if not use_native else _parse_ornith_thinking(r)[1]):
                    fmt_ok += 1
            mode_results["format_compliance"] = fmt_ok / 2.0

            results[mode] = mode_results

    finally:
        settings.ORNITH_NATIVE_ADAPTATION = orig_flag
        gn._llm_chat = orig_llm

    return results


def print_report(results):
    print("\n" + "="*70)
    print("EMPIRICAL VALIDATION: Ornith Adaptation in Aether")
    print("="*70)

    for mode in ["legacy", "native"]:
        r = results[mode]
        print(f"\n--- MODE: {mode.upper()} (ORNITH_NATIVE_ADAPTATION={mode=='native'}) ---")
        print(f"Extraction success rate     : {r['extraction_success_rate']*100:.1f}%")
        print(f"Pollution rate (think leak) : {r['pollution_rate']*100:.1f}%")
        print(f"Avg prompt size (chars)     : {r['prompt_sizes'][-1] if r['prompt_sizes'] else 'N/A'}")
        print(f"Long context prompt size    : {r['long_context_prompt_len']}")
        print(f"Multi-step LLM calls (sim)  : {r['multi_step_calls']}")
        print(f"Error recovery rate         : {r['error_recovery']*100:.1f}%")
        print(f"Format compliance proxy     : {r['format_compliance']*100:.1f}%")

        # Parse quality aggregate
        think_removed = sum(1 for p in r["parse_quality"] if p["think_removed"])
        print(f"Think cleanly removed       : {think_removed}/{len(r['parse_quality'])} cases")

    # Decision logic
    print("\n" + "="*70)
    print("ANALYSIS & RECOMMENDATIONS")
    print("="*70)

    leg = results["legacy"]
    nat = results["native"]

    deltas = {}
    deltas["extraction"] = nat["extraction_success_rate"] - leg["extraction_success_rate"]
    deltas["pollution"] = nat["pollution_rate"] - leg["pollution_rate"]
    deltas["prompt_size"] = (nat["prompt_sizes"][-1] if nat["prompt_sizes"] else 0) - (leg["prompt_sizes"][-1] if leg["prompt_sizes"] else 0)
    deltas["long_prompt"] = nat["long_context_prompt_len"] - leg["long_context_prompt_len"]
    deltas["recovery"] = nat["error_recovery"] - leg["error_recovery"]

    print("\nDelta (native - legacy):")
    for k, v in deltas.items():
        sign = "+" if v > 0 else ""
        print(f"  {k:20s}: {sign}{v:.3f}")

    # Objective decision
    reverts = []
    if deltas["extraction"] < -0.05:
        reverts.append("parsing / native path reduced extraction success")
    if deltas["pollution"] > 0.05:
        reverts.append("native path increased pollution")
    if deltas["prompt_size"] > 2000:
        reverts.append("significantly larger prompts (token cost)")
    if deltas["recovery"] < -0.1:
        reverts.append("worse error recovery")

    if reverts:
        print("\n⚠️  REGRESSIONS DETECTED:")
        for r in reverts:
            print(f"   - {r}")
        print("\nRecommendation: Revert the offending changes (set ORNITH_NATIVE_ADAPTATION=False by default or selectively).")
    else:
        print("\n✅ No major regressions detected in this battery.")
        print("Native adaptations appear neutral or beneficial on these simulated metrics.")

    print("\nNote: These are mock-based measurements (prompt size, parsing success, extraction).")
    print("Real model quality (reasoning coherence, actual tool selection accuracy) requires")
    print("running against a live Ornith instance with human or benchmark judgment.")


if __name__ == "__main__":
    results = run_battery()
    print_report(results)
