"""
Constructor del agente CrewAI.
"""
from crewai import Agent, LLM

from core.config.settings import MODELO_LITELLM, OLLAMA_HOST
from core.memory.context_builder import construir_contexto_memoria
from core.tools.web_search import buscar_web
from core.tools.url_reader import leer_url
from core.agent.prompts import (
    AGENTE_ROLE,
    AGENTE_GOAL,
    construir_backstory,
)


def construir_agente(mem: dict, con_tools: bool = True) -> Agent:
    """
    Construye el agente CrewAI con herramientas opcionales.
    Inyecta contexto de memoria en el backstory.
    """
    contexto_memoria = construir_contexto_memoria(mem)
    backstory = construir_backstory(contexto_memoria)
    
    _llm_crew = LLM(
        model=MODELO_LITELLM,
        base_url=f"{OLLAMA_HOST}",
        api_key="ollama",
        max_tokens=4096,
        temperature=0.1,
        extra_body={
            "options": {
                "num_ctx":     16384,
                "temperature": 0.1,
            }
        },
    )
    
    return Agent(
        role=AGENTE_ROLE,
        goal=AGENTE_GOAL,
        backstory=backstory,
        verbose=False,
        llm=_llm_crew,
        tools=[buscar_web, leer_url] if con_tools else [],
        max_iter=3,
        max_retry_limit=2,
        respect_context_window=True,
        max_rpm=10,
    )
