"""
Lectura y limpieza de contenido HTML desde URLs.
"""
import re
import requests
from langchain_core.tools import tool


_HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _limpiar_html(html: str) -> str:
    """
    Limpia HTML removiendo scripts, estilos y etiquetas innecesarias.
    Extrae texto semántico de article/main o fallback a todo.
    """
    html_original = html
    for tag in ("head", "script", "style", "nav", "header", "footer", "aside"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html,
                      flags=re.DOTALL | re.IGNORECASE)
    
    html_semantico = html
    for container in ("article", "main"):
        m = re.search(rf"<{container}[^>]*>(.*?)</{container}>", html,
                      flags=re.DOTALL | re.IGNORECASE)
        if m:
            html_semantico = m.group(1)
            break
    
    text_semantico = re.sub(r"<[^>]+>", " ", html_semantico)
    text_semantico = re.sub(r"\s{2,}", " ", text_semantico).strip()
    
    if len(text_semantico) >= 300:
        return text_semantico
    
    text_completo = re.sub(r"<[^>]+>", " ", html_original)
    text_completo = re.sub(r"\s{2,}", " ", text_completo).strip()
    return text_completo


def _desempaquetar_ddg(url: str) -> str:
    """Desempaqueta URLs redirigidas de DuckDuckGo."""
    import urllib.parse
    if "duckduckgo.com/l/?" in url:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            return qs.get("uddg", [url])[0]
        except Exception:
            pass
    return url


@tool("Leer Contenido de una URL")
def leer_url(url: str) -> str:
    """Accede a una URL y extrae el texto principal del artículo o página."""
    url = _desempaquetar_ddg(url)
    print(f"\n📖 [Javier NAVEGANDO]: {url}")
    try:
        r = requests.get(url, headers=_HEADERS_WEB, timeout=12)
        if r.status_code == 200:
            texto = _limpiar_html(r.text)[:7000]
            print(f"⚡ Extraídos {len(texto):,} caracteres limpios.")
            return f"[CONTENIDO DE {url}]\n{texto}"
        return f"HTTP {r.status_code} al intentar leer la página."
    except Exception as e:
        return f"Error al navegar la URL: {e}"
