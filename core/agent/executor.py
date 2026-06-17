"""
Executor de tareas CrewAI con manejo robusto de errores.
"""
from crewai import Task, Crew, LLM
from langchain_openai import ChatOpenAI

from core.config.settings import MODELO, OLLAMA_HOST
from core.agent.prompts import construir_task_description


def crear_tarea(orden: str, agente) -> Task:
    """Crea una Task de CrewAI con descripción dinámico."""
    return Task(
        description=construir_task_description(orden),
        expected_output="Un informe sofisticado con todos los datos reales y ordenados.",
        agent=agente,
    )


def ejecutar_crew(agente, tarea: Task) -> str:
    """
    Ejecuta el Crew con manejo robusto de errores.
    En caso de fallo, hace fallback directo al LLM de ChatOpenAI.
    """
    try:
        canal = Crew(agents=[agente], tasks=[tarea], verbose=0)
        return str(canal.kickoff())
    except Exception as e:
        err_str = str(e)
        if any(kw in err_str for kw in [
            "AgentAction", "tool_input", "validation error", "LLM Provider",
            "OutputParserException", "Invalid Format", "missed the 'Action'",
            "Parsing LLM output produced", "Could not parse LLM output",
        ]):
            print("⚠️  [SISTEMA]: Reintentando con modo de compatibilidad (LLM directo)...")
            try:
                _llm_direct = ChatOpenAI(
                    model=MODELO,
                    openai_api_key="ollama",
                    openai_api_base=f"{OLLAMA_HOST}/v1",
                    max_tokens=4096,
                    temperature=0.1,
                )
                prompt_fallback = (
                    f"{tarea.description}\n\n"
                    "Responde directamente con la información solicitada basándote "
                    "en tu conocimiento y en cualquier dato que hayas podido obtener."
                )
                return _llm_direct.invoke(prompt_fallback).content
            except Exception as e2:
                return f"Error en modo de compatibilidad: {e2}"
        raise
