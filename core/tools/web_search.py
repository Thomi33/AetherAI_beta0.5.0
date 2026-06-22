"""
Búsqueda web con SearXNG y fallback DuckDuckGo.
"""
import urllib.parse
import requests
import re

from core.config.settings import SEARXNG_URL
from langchain_core.tools import tool


_HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_HEADERS_JSON = {
    "User-Agent": _HEADERS_WEB["User-Agent"],
    "Accept":     "application/json",
}


def _desempaquetar_ddg(url: str) -> str:
    """Desempaqueta URLs redirigidas de DuckDuckGo."""
    if "duckduckgo.com/l/?" in url:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            return qs.get("uddg", [url])[0]
        except Exception:
            pass
    return url


@tool("Buscar en la Web con SearXNG")
def buscar_web(query: str) -> str:
    """Busca en internet y devuelve títulos, URLs y resúmenes de los resultados."""
    print(f"\n🔍 [Aether BUSCANDO]: '{query}'...")
    
    # Intentar con SearXNG
    try:
        r = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json",
                    "engines": "google,duckduckgo,wikipedia", "safesearch": "1"},
            headers=_HEADERS_JSON, timeout=6,
        )
        if r.status_code == 200:
            resultados = r.json().get("results", [])[:5]
            if resultados:
                lineas = []
                for res in resultados:
                    t, u = res.get("title", ""), res.get("url", "")
                    print(f"  → {t}  [{u}]")
                    lineas.append(f"- Título: {t}\n  URL: {u}\n  Resumen: {res.get('content','')}")
                return "[SEARXNG]\n" + "\n".join(lineas)
    except Exception:
        pass
    
    # Fallback con DuckDuckGo
    try:
        r = requests.get(
            f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}",
            headers=_HEADERS_WEB, timeout=10,
        )
        if r.status_code == 200:
            titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', r.text, re.DOTALL)   
            links  = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"', r.text, re.DOTALL)
            if not titles:
                return "Sin resultados disponibles para esa consulta, perdoname."
            lineas = []
            for i, t in enumerate(titles):
                t_clean = re.sub(r"<[^>]+>", "", t).strip()
                l_clean = _desempaquetar_ddg(links[i].strip()) if i < len(links) else ""
                print(f"  → {t_clean}  [{l_clean}]")
                lineas.append(f"- Título: {t_clean}\n  URL: {l_clean}")
            return "[DUCKDUCKGO FALLBACK]\n" + "\n".join(lineas)
    except Exception as e:
        return f"Error en todos los sistemas de búsqueda: {e}"
    
    return "Imposible conectar con ningún motor de búsqueda, perdoname."
