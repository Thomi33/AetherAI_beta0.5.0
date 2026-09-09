# Auditoría de Arquitectura - Proyecto Aether

## Opinión General
Aether tiene una base sólida y una visión clara: ser un "Jarvis local" proactivo. La migración a **LangGraph** fue un acierto arquitectónico, ya que proporciona la estructura necesaria para gestionar estados complejos y ciclos de retroalimentación que serían inmanejables en un flujo lineal.

El punto más fuerte del proyecto es el **Error Handler**. La implementación de un loop de diagnóstico (Web $\rightarrow$ LLM $\rightarrow$ Fix $\rightarrow$ Retry) es una característica de nivel avanzado que dota al agente de una capacidad de "autocuración" muy valiosa.

---

## Análisis de la Arquitectura

### 1. El "Cuello de Botella" del Routing
Actualmente, Aether sufre de una **disonancia cognitiva arquitectónica**:
- Tiene un sistema de recuperación (Error Handler) sofisticado.
- Pero tiene un sistema de dirección (Router) primitivo basado en palabras clave y regex.

El `nodo_router` actúa como un filtro determinista que a menudo bloquea la capacidad de razonamiento del LLM. Si el usuario no usa la palabra "busca", el agente podría no ir a la web aunque sea la herramienta obvia para resolver la tarea. Esto contradice la filosofía de "orientación a objetivos" del proyecto.

### 2. Fragmentación de Herramientas
Existe una redundancia en cómo se ejecutan las acciones:
- Algunas pasan por el `TOOL_REGISTRY`.
- Otras son detectadas por regex en `_despues_de_general` (ej. escritura de archivos).
- Otras son llamadas nativas de tool calling en `nodo_web`.

Esta fragmentación hace que el mantenimiento sea difícil y que el comportamiento del agente sea inconsistente.

### 3. Debilidad en Operaciones de Archivos
La escritura de archivos es el "patito feo" de la implementación actual. Depende de regex para adivinar rutas y a veces se solapa con el `shell_executor`. No existe una herramienta de sistema de archivos de primera clase que gestione directorios y múltiples archivos de forma atómica.

---

## Propuestas de Mejora (Enfoque Ponytail)

Para simplificar el código y aumentar la robustez, recomiendo los siguientes cambios:

### 🚀 1. Unificación del Loop de Razonamiento
**Eliminar el `nodo_router` y las transiciones basadas en keywords.**
En su lugar, implementar un loop de **ReAct (Reasoning and Acting)**:
- **Nodo de Razonamiento**: El LLM recibe el estado y el catálogo de `TOOL_REGISTRY` (vía `construir_tools_ollama`).
- **Decisión**: El LLM decide si llama a una herramienta o responde al usuario.
- **Ejecución**: Un único nodo ejecutor procesa la herramienta seleccionada y devuelve el resultado al Nodo de Razonamiento.
- **Resultado**: El grafo se simplifica drásticamente y el agente se vuelve mucho más flexible.

### 📂 2. Herramienta de Sistema de Archivos (FileSystemTool)
Sustituir la lógica dispersa de `nodo_escribir_archivo` y los heredocs de shell por una herramienta dedicada que soporte:
- `write_file(path, content)`
- `read_file(path)`
- `make_dir(path)`
- `list_dir(path)`
Esto elimina la necesidad de que el LLM "adivine" comandos de shell para tareas básicas de archivos.

### 🧠 3. Memoria Activa
Convertir la memoria de un "complemento del prompt" a una "herramienta explícita". Permitir que el agente decida cuándo `guardar_dato()` o `consultar_memoria()`, en lugar de depender únicamente de la compactación automática cada 12 turnos.

---

## Veredicto Final
**Estado:** Muy prometedor, pero con "grasa" en la capa de routing y "huecos" en la capa de herramientas de archivos. 
**Prioridad:** Cambiar el routing determinista $\rightarrow$ LLM Reasoning.
