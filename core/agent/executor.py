"""
Executor de tareas CrewAI con manejo robusto de errores.

OPTIMIZACIONES vs versión anterior:
- ChatOpenAI del fallback es singleton (no se re-instancia en cada fallo)
"""
from crewai import Task, Crew
from langchain_openai import ChatOpenAI

from core.config.settings import MODELO, OLLAMA_HOST
from core.agent.prompts import construir_task_description

# Singleton para el fallback — se crea solo si se necesita y solo una vez
_llm_fallback: ChatOpenAI | None = None

_ERRORES_PARSING = frozenset([
    "AgentAction", "tool_input", "validation error", "LLM Provider",
    "OutputParserException", "Invalid Format", "missed the 'Action'",
    "Parsing LLM output produced", "Could not parse LLM output",
])


def _get_llm_fallback() -> ChatOpenAI:
    """Retorna el LLM de fallback (singleton)."""
    global _llm_fallback
    if _llm_fallback is None:
        _llm_fallback = ChatOpenAI(
            model=MODELO,
            openai_api_key="ollama",
            openai_api_base=f"{OLLAMA_HOST}/v1",
            max_tokens=4096,
            temperature=0.1,
            streaming=True,
        )
    return _llm_fallback


def crear_tarea(orden: str, agente) -> Task:
    """Crea una Task de CrewAI con descripción dinámica."""
    return Task(
        description=construir_task_description(orden),
        expected_output="Un informe sofisticado con todos los datos reales y ordenados.",
        agent=agente,
    )


def ejecutar_crew(agente, tarea: Task, on_token=None) -> str:
    """
    Ejecuta el Crew con manejo robusto de errores.
    En caso de fallo de parsing, hace fallback directo al LLM cacheado con streaming.

    on_token: callable opcional — se llama con cada token del fallback LLM.
              Si CrewAI tiene éxito, no se usa (no hay streaming en CrewAI).
    """
    try:
        canal = Crew(agents=[agente], tasks=[tarea], verbose=0)
        return str(canal.kickoff())
    except Exception as e:
        err_str = str(e)
        if any(kw in err_str for kw in _ERRORES_PARSING):
            print("⚠️  [SISTEMA]: Reintentando con modo de compatibilidad (LLM directo)...")
            try:
                prompt_fallback = (
                    f"{tarea.description}\n\n"
                    "Responde directamente con la información solicitada basándote "
                    "en tu conocimiento y en cualquier dato que hayas podido obtener."
                )
                respuesta = ""
                for chunk in _get_llm_fallback().stream(prompt_fallback):
                    token = chunk.content
                    if on_token:
                        on_token(token)
                    respuesta += token
                return respuesta
            except Exception as e2:
                return f"Error en modo de compatibilidad: {e2}"
        raise