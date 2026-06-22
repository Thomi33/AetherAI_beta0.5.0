"""
Constructor del agente CrewAI.

OPTIMIZACIONES:
- LLM singleton (una instancia para siempre)
- Dos cachés independientes: agente con tools y sin tools
  → cada uno se reconstruye solo si cambia el contexto de memoria
- max_iter=5 para el agente con tools (web: buscar + leer URL = 2 pasos mínimo)
"""
import hashlib
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

# ─────────────────────────────────────────────
# Singletons de módulo
# ─────────────────────────────────────────────
_llm_singleton: LLM | None = None

# Dos cachés separados: uno por modo
_agente_sin_tools: Agent | None = None
_agente_con_tools: Agent | None = None
_hash_sin_tools:   str = ""
_hash_con_tools:   str = ""


def _get_llm() -> LLM:
    """LLM singleton — se instancia una sola vez en toda la sesión."""
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = LLM(
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
    return _llm_singleton


def construir_agente(mem: dict, con_tools: bool = True) -> Agent:
    """
    Retorna el agente CrewAI adecuado para la tarea.

    - con_tools=True  → agente con buscar_web + leer_url (para consultas web)
    - con_tools=False → agente ligero solo para comandos/texto (más rápido)

    Cada variante tiene su propio caché y se reconstruye solo si la memoria cambió.
    """
    global _agente_sin_tools, _agente_con_tools
    global _hash_sin_tools,   _hash_con_tools

    contexto_memoria = construir_contexto_memoria(mem)
    nuevo_hash = hashlib.md5(contexto_memoria.encode()).hexdigest()

    if con_tools:
        if _agente_con_tools is None or nuevo_hash != _hash_con_tools:
            backstory = construir_backstory(contexto_memoria)
            _agente_con_tools = Agent(
                role=AGENTE_ROLE,
                goal=AGENTE_GOAL,
                backstory=backstory,
                verbose=False,
                llm=_get_llm(),
                tools=[buscar_web, leer_url],
                max_iter=5,           # buscar + leer URL + responder = mínimo 3 pasos
                max_retry_limit=2,
                respect_context_window=True,
                max_rpm=10,
            )
            _hash_con_tools = nuevo_hash
        return _agente_con_tools

    else:
        if _agente_sin_tools is None or nuevo_hash != _hash_sin_tools:
            backstory = construir_backstory(contexto_memoria)
            _agente_sin_tools = Agent(
                role=AGENTE_ROLE,
                goal=AGENTE_GOAL,
                backstory=backstory,
                verbose=False,
                llm=_get_llm(),
                tools=[],
                max_iter=3,
                max_retry_limit=2,
                respect_context_window=True,
                max_rpm=10,
            )
            _hash_sin_tools = nuevo_hash
        return _agente_sin_tools