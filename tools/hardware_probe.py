#!/usr/bin/env python3
"""hardware_probe.py — perfila CPU/RAM/VRAM/disco y recomienda tier + modelo.

Sin dependencias externas (solo stdlib) para correr con el python del
sistema ANTES de crear el venv. Salida: JSON por stdout + resumen humano
por stderr. Cero recortes de features: solo ajusta MODELO/VISION/CTX.
Uso: python3 tools/hardware_probe.py [--json] [--model-override X]
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys

CATALOGO = [
    # (tier, min_usable_gb, modelo, vision, ctx, predict, planner, chat, max_t,
    #  batch, keep, parallel, maxmodels, nota)
    ("POTATO", 0.0, "qwen2.5:1.5b", "moondream", 4096, 1024, 768, 4, 80,
     64, "5m", 1, 1, "iGPU/Celeron o <=6GB: agente chico pero con tools"),
    ("LOW", 3.0, "qwen2.5:3b", "moondream", 8192, 2048, 1024, 6, 120,
     128, "5m", 1, 1, "8-12GB o <4GB VRAM: equilibrio N4500"),
    ("MID", 5.0, "qwen2.5:7b", "minicpm-v4.6:latest", 16384, 3072, 1536, 8,
     160, 256, "15m", 2, 1, "16GB o 6-10GB VRAM: agente pleno"),
    ("HIGH", 9.0, "ornith-1.5:9b", "qwen2.5vl:7b", 32768, 4096, 2048, 10,
     200, 512, "30m", 4, 2, "12GB+ VRAM o 32GB RAM: Ornith 9B"),
    ("ULTRA", 16.0, "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M",
     "qwen2.5vl:7b", 32768, 8192, 3072, 10, 200, 512, "-1", 4, 2,
     "24GB+ VRAM: Ornith 35B"),
]

def _run(cmd, timeout=4):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except Exception:
        return ""

def ram_gb():
    try:
        kb = int(re.search(r"MemTotal:\s+(\d+)", open("/proc/meminfo").read()).group(1))
        return round(kb / 1024 / 1024, 1)
    except Exception:
        return 0.0

def cpu_info():
    model, hilos = "?", 0
    try:
        txt = open("/proc/cpuinfo").read()
        m = re.search(r"model name\s+:\s+(.+)", txt)
        if m: model = m.group(1).strip()
    except Exception: pass
    try: hilos = os.cpu_count() or 0
    except Exception: pass
    return model, hilos

def gpu_info():
    """(vram_gb, nombre, backend). 0 VRAM = CPU/iGPU compartida."""
    # NVIDIA
    if shutil.which("nvidia-smi"):
        s = _run(["nvidia-smi", "--query-gpu=memory.total,name",
                  "--format=csv,noheader,nounits"])
        if s:
            try:
                mem, name = s.splitlines()[0].split(",", 1)
                return round(float(mem) / 1024, 1), name.strip(), "nvidia"
            except Exception: pass
    # AMD ROCm
    if shutil.which("rocm-smi"):
        s = _run(["rocm-smi", "--showmeminfo", "vram"])
        m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", s, re.I)
        if m:
            v = float(m.group(1)) / (1 if m.group(2).upper() == "GB" else 1024)
            return round(v, 1), "AMD (rocm-smi)", "rocm"
    # Intel iGPU / genérico vía lspci
    lspci = _run(["lspci"]) if shutil.which("lspci") else ""
    m = re.search(r"(VGA|3D|Display).*?:\s*(.+)", lspci)
    nombre = m.group(2).strip()[:80] if m else "sin dGPU (CPU/iGPU)"
    return 0.0, nombre, "shared"
def resto():
    model, hilos = cpu_info()
    ram = ram_gb()
    vram, gpuname, backend = gpu_info()
    try:
        free = shutil.disk_usage(os.path.expanduser("~")).free / 1e9
    except Exception:
        free = -1.0
    swap = 0.0
    try:
        m = re.search(r"SwapTotal:\s+(\d+)", open("/proc/meminfo").read())
        if m:
            swap = round(int(m.group(1)) / 1024 / 1024, 1)
    except Exception:
        pass
    weak_cpu = bool(re.search(r"celeron|atom|pentium|n4500|n4020|n5100|n100", model, re.I))
    usable = vram if vram >= 3.0 else max(0.0, round((ram - 3.5) * 0.55, 1))
    if weak_cpu and usable > 3.0:
        usable = 3.0
    tier = CATALOGO[0]
    for c in CATALOGO:
        if usable >= c[1]:
            tier = c
    t, _, modelo, vision, ctx, pred, plan, chat, maxt, batch, keep, par, mm, nota = tier
    ctx_efectivo = min(ctx * 2, 65536)
    threads = max(1, min(hilos - 1 if hilos and hilos > 2 else (hilos or 4), 8))
    if weak_cpu:
        threads = min(threads, 3)
    num_gpu = 0 if vram < 3.0 else (8 if vram < 12 else (10 if vram < 20 else 24))
    return {
        "cpu": model, "hilos": hilos, "ram_gb": ram, "swap_gb": swap,
        "vram_gb": vram, "gpu": gpuname, "gpu_backend": backend,
        "disco_libre_gb": round(free, 1), "usable_gb": usable,
        "weak_cpu": weak_cpu, "tier": t, "nota": nota,
        "recomendado": {
            "MODELO": modelo, "MODELO_VISION": modelo,
            "NUM_CTX_BASE": ctx, "NUM_CTX": ctx_efectivo,
            "NUM_PREDICT": pred, "NUM_PREDICT_PLANNER": plan,
            "MAX_TURNOS_CONTEXTO_CHAT": chat, "TIMEOUT_CMD": maxt,
            "OLLAMA_KEEP_ALIVE": keep, "OLLAMA_NUM_PARALLEL": par,
            "OLLAMA_MAX_LOADED_MODELS": mm,
            "OLLAMA_GEN_OPTIONS": {"num_batch": batch, "num_thread": threads, "num_gpu": num_gpu},
        },
    }

def por_tier(nombre, base):
    """Devuelve recomendado del tier pedido, conservando threads/gpu detectados."""
    for c in CATALOGO:
        if c[0] == nombre:
            t, _, modelo, vision, ctx, pred, plan, chat, maxt, batch, keep, par, mm, nota = c
            gen = dict(base["recomendado"].get("OLLAMA_GEN_OPTIONS", {}))
            gen["num_batch"] = batch
            # threads/num_gpu detectados se conservan (ya contemplan CPU/GPU real)
            return {"tier": t, "nota": nota,
                    "recomendado": {"MODELO": modelo, "MODELO_VISION": modelo,
                                    "NUM_CTX_BASE": ctx, "NUM_CTX": min(ctx * 2, 65536),
                                    "NUM_PREDICT": pred,
                                    "NUM_PREDICT_PLANNER": plan,
                                    "MAX_TURNOS_CONTEXTO_CHAT": chat,
                                    "TIMEOUT_CMD": maxt, "OLLAMA_KEEP_ALIVE": keep,
                                    "OLLAMA_NUM_PARALLEL": par,
                                    "OLLAMA_MAX_LOADED_MODELS": mm,
                                    "OLLAMA_GEN_OPTIONS": gen}}
    return None


def main():
    ov = ""
    force = ""
    if "--model-override" in sys.argv:
        try:
            ov = sys.argv[sys.argv.index("--model-override") + 1]
        except IndexError:
            ov = ""
    if "--force-tier" in sys.argv:
        try:
            force = sys.argv[sys.argv.index("--force-tier") + 1].upper()
        except IndexError:
            force = ""
    r = resto()
    if force:
        f = por_tier(force, r)
        if f:
            r["tier"] = f["tier"]
            r["nota"] = f["nota"]
            r["recomendado"] = f["recomendado"]
        else:
            print(f"tier desconocido: {force} (uso {r['tier']})", file=sys.stderr)
    if ov:
        r["recomendado"]["MODELO"] = ov
    print(json.dumps(r, indent=2, ensure_ascii=False))
    msg = (f"[{r['tier']}] CPU={r['cpu']} RAM={r['ram_gb']}GB VRAM={r['vram_gb']}GB "
           f"usable~{r['usable_gb']}GB -> {r['recomendado']['MODELO']} ctx={r['recomendado']['NUM_CTX']}")
    print(msg, file=sys.stderr)

if __name__ == "__main__":
    main()
