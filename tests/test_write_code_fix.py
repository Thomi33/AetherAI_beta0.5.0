"""Test aislado de las correcciones de escritura de código de Aether."""
import os
import sys

sys.path.insert(0, "/home/thomi/mi_proyecto_crew")

from core.agent import graph_nodes as gn

FALLOS = []


def check(nombre, cond, detalle=""):
    if not cond:
        FALLOS.append(f"{nombre}: {detalle}")


# ── 1) _resolver_ruta_destino_codigo ─────────────────────────────
cwd = os.getcwd()

r = gn._resolver_ruta_destino_codigo("creá un archivo main.py que imprima hola", None)
check("resolver.nombre_simple", r == os.path.abspath("main.py"), r)

r = gn._resolver_ruta_destino_codigo("escribime un script en sub/foo.py", None)
check("resolver.subdir", r == os.path.abspath("sub/foo.py"), r)

r = gn._resolver_ruta_destino_codigo("guarda el código en /tmp/x.sh", None)
check("resolver.abs", r == "/tmp/x.sh", r)

r = gn._resolver_ruta_destino_codigo("hacé algo con la info", None)
check("resolver.sin_ruta", r is None, r)

r = gn._resolver_ruta_destino_codigo("generá un script", "main.py")
check("resolver.via_args", r == os.path.abspath("main.py"), r)


# ── 2) node_codigo escribe el archivo pedido ─────────────────────
DEST = "/tmp/aether_test_demo.py"
if os.path.exists(DEST):
    os.unlink(DEST)

estado = {
    "orden": "creá un archivo que imprima hola y guardálo",
    "mem": {"conversacion": [], "core_memory": {}, "recuerdos": [], "flatpaks": {}},
    "modo_autonomo": True,
    "plan_pasos": [{"tool": "codigo", "instruccion": "creá un script", "args": {"filename": DEST}}],
    "plan_index": 0,
    "plan_resultados": [],
}

orig_llm = gn._llm_chat
orig_parse = gn._parse_ornith_thinking
gn._llm_chat = lambda system, user, **kw: "```python\nprint('hola desde el test')\n```"

try:
    salida = gn.node_codigo(estado)
finally:
    gn._llm_chat = orig_llm

check("codigo.archivo_creado", os.path.exists(DEST), DEST)
if os.path.exists(DEST):
    with open(DEST, encoding="utf-8") as f:
        check("codigo.contenido", "print('hola desde el test')" in f.read(), "no está el fuente")
check("codigo._archivo_codigo", salida.get("_archivo_codigo") == DEST, salida.get("_archivo_codigo"))
check("codigo._codigo_original", "print('hola desde el test')" in (salida.get("_codigo_original") or ""), salida.get("_codigo_original"))


# ── 3) node_file_write detrás de codigo guarda el FUENTE ─────────
F_OUT = "/tmp/aether_test_out.py"
if os.path.exists(F_OUT):
    os.unlink(F_OUT)

estado_fw = {
    "orden": "guardá el código generado en un archivo",
    "mem": {"conversacion": [], "core_memory": {}, "recuerdos": []},
    "plan_pasos": [
        {"tool": "codigo", "instruccion": "generar", "args": {}},
        {"tool": "file_write", "instruccion": "guardar", "args": {"filename": F_OUT}},
    ],
    "plan_index": 1,
    "plan_resultados": ["42"],   # stdout del script (lo que guardaba ANTES: bug)
    "_codigo_original": "print('hola desde el test')",
}

salida_fw = gn.node_file_write(estado_fw)
check("fw.algo_guardó", os.path.exists(F_OUT), F_OUT)
if os.path.exists(F_OUT):
    with open(F_OUT, encoding="utf-8") as f:
        contenido_fw = f.read()
    check("fw.guarda_fuente", "print('hola desde el test')" in contenido_fw, contenido_fw)
    check("fw.no_guarda_salida", contenido_fw.strip() != "42", contenido_fw)
check("fw.sin_error", not salida_fw.get("error_activo"), salida_fw)


# ── LIMPIEZA ──────────────────────────────────────────────────────
for p in (DEST, F_OUT):
    if os.path.exists(p):
        try:
            os.unlink(p)
        except OSError:
            pass

if FALLOS:
    print("FALLOS:")
    for f in FALLOS:
        print("  -", f)
    sys.exit(1)
print("OK: todas las verificaciones pasaron.")