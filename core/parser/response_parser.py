"""
Parsing de respuestas del LLM y análisis de salida.
"""
from langchain_openai import ChatOpenAI

from core.config.settings import MODELO, OLLAMA_HOST


def analizar_salida(orden: str, comando: str, salida: str) -> str:
    """
    Analiza salida de comando con el LLM directo (sin CrewAI).
    Retorna un reporte ejecutivo breve.
    """
    _llm_direct = ChatOpenAI(
        model=MODELO,
        openai_api_key="ollama",
        openai_api_base=f"{OLLAMA_HOST}/v1",
        max_tokens=4096,
        temperature=0.1,
    )
    
    prompt = (
        f'El Creador ordenó: "{orden}". Se ejecutó: `{comando}`.\n'
        f'Resultado:\n"""\n{salida[:3000]}\n"""\n\n'
        "Redacta un reporte ejecutivo breve e impecable para el Creador."
    )
    try:
        return _llm_direct.invoke(prompt).content
    except Exception as e:
        return f"No pude analizar la salida: {e}"
