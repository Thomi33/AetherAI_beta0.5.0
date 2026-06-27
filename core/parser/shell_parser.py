"""
Parsing de comandos shell en formato [SHELL] ... [/SHELL].
"""
import re


def extraer_comando_shell(texto: str) -> str | None:
    """
    Extrae comando shell del formato [SHELL]comando[/SHELL].
    Fallback a bloques de código Markdown CON lenguaje shell explícito.

    NOTA: el fallback exige un tag de lenguaje (```bash/sh/zsh/shell). Antes
    el tag era opcional y capturaba CUALQUIER bloque ``` — incluido el output
    que el LLM alucina entre fences (p.ej. una salida fake de `lscpu`), que
    terminaba ejecutándose como comando ('zsh: no matches found: ...').
    """
    m = re.search(r"\[SHELL\]\s*(.*?)\s*\[/SHELL\]", texto, re.DOTALL)
    if m:
        return m.group(1).strip()

    m = re.search(r"```(?:bash|sh|zsh|shell)\s*\n(.*?)\n```", texto, re.DOTALL)
    if m:
        cmd = m.group(1).strip()
        if cmd:
            return cmd

    return None
