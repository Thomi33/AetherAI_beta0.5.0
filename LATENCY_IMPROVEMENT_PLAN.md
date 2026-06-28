# 🚀 Plan de Mejora de Latencia — Aether

## Objetivo
Disminuir la latencia percibida y real del agente **sin sacrificar la calidad** de las respuestas ni la robustez del grafo LangGraph.

---

## 📊 Análisis Actual de Cuellos de Botella

### 1. Llamadas al LLM (Ollama)
- **`_llm_chat()`** se llama **~10-15 veces por orden compleja**:
  - `node_planner`: 1 llamada (fallback clasificador)
  - `_planner_llm`: 1 llamada (multi-tool)
  - `node_web`: 1 llamada (síntesis)
  - `node_shell`: 1-2 llamadas (generación + reintento)
  - `node_codigo`: 1 llamada
  - `node_text`: 1 llamada
  - `node_plan_synthesizer`: 1 llamada
  - `node_error_diagnose`: 1 llamada
  - `node_error_fallback`: 1 llamada (código)

Cada llamada tiene:
- **Cold start** si el modelo no está en VRAM (2-5 segundos)
- **Prefill** proporcional al contexto inyectado (~100-300ms con 200 turnos)
- **Generación** dependiente de tokens de salida

### 2. Configuración Actual (`settings.py`)
```python
OLLAMA_KEEP_ALIVE = "30m"       # ✅ Bien, pero podría ser "-1"
OLLAMA_GEN_OPTIONS = {
    "num_batch": 512,           # ✅ Buen valor
    # "num_gpu": 999,           # ❌ COMENTADO (debería estar activo si hay GPU)
    # "num_thread": 16,         # ❌ COMENTADO
}
NUM_CTX = 16384                 # ⚠️ Alto, pero necesario para 200 turnos
MAX_TURNOS_CONTEXTO = 200       # ⚠️ Excesivo para la mayoría de órdenes
MAX_TURNOS_CONTEXTO_CHAT = 10   # ✅ Bien acotado
MAX_TURNOS_CONTEXTO_PLAN = 0    # ✅ Excelente decisión
```

### 3. Flujo del Grafo
```
START → planner → context_manager → plan_executor → ... → finalize
```
- **`context_manager`** añade overhead (construye contexto completo ANTES de cada inferencia)
- **No hay caché** de prompts o resultados intermedios
- **Streaming** solo en la respuesta final, no en pasos intermedios del plan

---

## 🔧 Mejoras Propuestas (Priorizadas por Impacto/Costo)

### 🔴 ALTA PRIORIDAD (Impacto: 30-50% reducción de latencia)

#### 1. Optimizar `OLLAMA_GEN_OPTIONS` para GPU
**Archivo:** `core/config/settings.py`

**Problema:** Las opciones de GPU están comentadas, forzando offload parcial a CPU.

**Solución:**
```python
OLLAMA_GEN_OPTIONS = {
    "num_batch": 512,
    "num_gpu": 999,      # ← FORZAR todas las capas a GPU (si VRAM >= 16GB)
    "num_thread": 8,     # ← Ajustar a núcleos físicos (no hyperthreading)
}
```

**Impacto esperado:** 40-60% más rápido en generación (GPU vs CPU mix).

**Riesgo:** Bajo. Si VRAM es insuficiente, Ollama hace fallback automático.

---

#### 2. Mantener Modelo Cargado Permanentemente
**Archivo:** `core/config/settings.py`

**Problema:** `OLLAMA_KEEP_ALIVE = "30m"` descarga el modelo tras 30 min de inactividad.

**Solución:**
```python
OLLAMA_KEEP_ALIVE = "-1"  # ← Nunca descargar (cargado para siempre)
```

**Impacto esperado:** Elimina 2-5 segundos de cold start en cada sesión.

**Riesgo:** Mínimo. Consume ~8-12 GB VRAM permanentemente (aceptable en sistemas con 16+ GB).

---

#### 3. Reducir `MAX_TURNOS_CONTEXTO` Dinámicamente
**Archivo:** `core/config/settings.py` + `core/memory/context_builder.py`

**Problema:** 200 turnos se inyectan SIEMPRE, incluso para órdenes simples como "Hola".

**Solución:** Implementar **ventana adaptativa** según tipo de orden:

```python
# settings.py
MAX_TURNOS_CONTEXTO_DYNAMIC = True
MAX_TURNOS_CONTEXTO_BASE = 20    # Para charla/consultas simples
MAX_TURNOS_CONTEXTO_MAX = 200    # Para tareas complejas multi-tool
```

```python
# context_builder.py
def construir_contexto_memoria(mem, tema="", orden=""):
    # Detectar complejidad de la orden
    if _es_orden_simple(orden):
        max_turnos = MAX_TURNOS_CONTEXTO_BASE
    else:
        max_turnos = MAX_TURNOS_CONTEXTO_MAX
    
    # Podar historial ANTES de construir el prompt
    conversacion = mem.get("conversacion", [])[-max_turnos:]
    ...
```

**Impacto esperado:** 50-70% menos tokens en prompt para órdenes simples → 100-200ms menos de prefill.

**Riesgo:** Bajo. Órdenes complejas mantienen el contexto completo.

---

#### 4. Caché de Prompts de Sistema
**Archivo:** `core/agent/graph_nodes.py`

**Problema:** `_system_prompt(mem, state)` se reconstruye en CADA llamada a `_llm_chat()`.

**Solución:** Cachear el prompt base y solo actualizar partes dinámicas:

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def _get_system_prompt_base(contexto_hash: str) -> str:
    """Construye el prompt base y lo cachea por contexto."""
    return construir_backstory(contexto)

def _system_prompt(mem: dict, state: "AetherState | None" = None) -> str:
    contexto = ...  # mismo código actual
    contexto_hash = hash(contexto)
    return _get_system_prompt_base(contexto_hash)
```

**Impacto esperado:** 10-20ms ahorrados por llamada × 10 llamadas = 100-200ms por orden.

**Riesgo:** Bajo. El hash detecta cambios en el contexto.

---

### 🟡 MEDIA PRIORIDAD (Impacto: 15-25% reducción)

#### 5. Parallelizar Búsqueda Web + Lectura de URL
**Archivo:** `core/agent/graph_nodes.py` (`node_web`)

**Problema:** `buscar_web()` y `leer_url()` son secuenciales.

**Solución:**
```python
import concurrent.futures

def node_web(state: AetherState) -> dict:
    ...
    resultados = buscar_web.invoke(orden)
    urls = re.findall(r"URL:\s*(https?://\S+)", resultados)
    
    if urls:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(leer_url.invoke, url) for url in urls[:3]]
            contenidos = [f.result() for f in concurrent.futures.as_completed(futures)]
        contenido_url = "\n".join(contenidos)[:3000]
    ...
```

**Impacto esperado:** 300-500ms menos en búsquedas web (lectura paralela de 3 URLs).

**Riesgo:** Medio. Aumentan requests simultáneos; ajustar `max_workers` según red.

---

#### 6. Pre-calentar Grafo en Background
**Archivo:** `core/agent/graph_builder.py` + `backend/core/aether_service.py`

**Problema:** `get_graph()` construye el grafo en la primera invocación (añade ~100ms).

**Solución:**
```python
# aether_service.py (al inicializar)
def initialize():
    ...
    # Pre-compilar grafo en background
    from core.agent.graph_builder import get_graph
    threading.Thread(target=get_graph, daemon=True).start()
```

**Impacto esperado:** 50-100ms menos en la primera orden de la sesión.

**Riesgo:** Bajo. Thread daemon no bloquea el inicio.

---

#### 7. Optimizar Context Manager (Evitar Doble Construcción)
**Archivo:** `core/agent/node_context_manager.py`

**Problema:** El contexto se construye en `node_context_manager` y luego se vuelve a procesar en `_system_prompt()`.

**Solución:** Pasar el contexto YA CONSTRUIDO directamente a los nodos:

```python
# node_context_manager.py
def node_context_manager(state: AetherState) -> dict:
    ...
    contexto = construir_contexto_memoria(mem, tema=tema)
    
    return {
        "context_slots": {
            "tema": tema,
            "orden": orden,
            "contexto": contexto,
            "contexto_ya_construido": True,  # ← Flag para saltar reconstrucción
        },
        ...
    }

# graph_nodes.py
def _system_prompt(mem: dict, state: "AetherState | None" = None) -> str:
    if state is not None:
        slots = state.get("context_slots") or {}
        if slots.get("contexto_ya_construido"):
            return slots["contexto"]  # ← Usar directo, sin reconstruir
        contexto = slots.get("contexto", "")
        if contexto:
            return construir_backstory(contexto)
    ...
```

**Impacto esperado:** 20-30ms ahorrados por nodo que usa `_system_prompt()`.

**Riesgo:** Bajo. Refactorización interna sin cambiar comportamiento.

---

### 🟢 BAJA PRIORIDAD (Impacto: 5-10% reducción)

#### 8. Batch de Múltiples Órdenes (Solo TUI)
**Archivo:** `tui/engine_bridge.py`

**Problema:** Cada orden en la TUI invoca el grafo por separado.

**Solución:** Agrupar órdenes rápidas consecutivas (ej: usuario escribe 2-3 preguntas seguidas):

```python
# engine_bridge.py
class _BatchProcessor:
    def __init__(self, window_ms=200):
        self.window_ms = window_ms
        self.buffer = []
        self.timer = None
    
    def add(self, orden: str):
        self.buffer.append(orden)
        if self.timer:
            self.timer.cancel()
        self.timer = threading.Timer(self.window_ms / 1000, self._procesar)
        self.timer.start()
    
    def _procesar(self):
        if len(self.buffer) == 1:
            yield from iter_eventos(self.buffer[0])
        else:
            # Procesar como plan multi-tool implícito
            ...
        self.buffer.clear()
```

**Impacto esperado:** Útil solo en TUI con usuarios que escriben rápido. 10-15% menos overhead.

**Riesgo:** Medio. Complejidad añadida; puede confundir si no se maneja bien el UX.

---

#### 9. Compresión de Historial Antiguo
**Archivo:** `core/memory/context_builder.py`

**Problema:** Turnos viejos (>50) ocupan espacio pero aportan poco contexto.

**Solución:** Resumir turnos antiguos en lugar de incluirlos completos:

```python
def _comprimir_historial(conversacion: list, max_turnos_recientes: int = 50) -> list:
    if len(conversacion) <= max_turnos_recientes:
        return conversacion
    
    recientes = conversacion[-max_turnos_recientes:]
    antiguos = conversacion[:-max_turnos_recientes]
    
    # Resumir antiguos con LLM (una sola vez, cachear resultado)
    resumen = _resumir_conversacion(antiguos)
    
    return [{"role": "system", "content": f"Resumen de conversación previa: {resumen}"}] + recientes
```

**Impacto esperado:** 10-20% menos tokens en sesiones largas (>100 turnos).

**Riesgo:** Medio. Pérdida potencial de detalles específicos del historial.

---

#### 10. Lazy Load de Tools Pesadas
**Archivo:** `core/agent/graph_nodes.py`

**Problema:** Imports de `web_search`, `vision`, etc., se hacen al inicio del módulo.

**Solución:** Importar bajo demanda dentro de cada nodo:

```python
def node_vision(state: AetherState) -> dict:
    from core.tools.vision import ver_pantalla  # ← Import lazy
    ...
```

**Impacto esperado:** 50-100ms menos en inicio del CLI/TUI.

**Riesgo:** Bajo. Python cachea imports, no hay impacto en llamadas subsiguientes.

---

## 📈 Métricas de Referencia (Antes vs Después Esperado)

| Escenario | Latencia Actual | Latencia Esperada | Mejora |
|-----------|----------------|-------------------|--------|
| Charla simple ("Hola") | 800-1200ms | 400-600ms | **50%** |
| Búsqueda web | 3-5s | 2-3s | **40%** |
| Launch app | 1-2s | 0.5-1s | **50%** |
| Shell comando | 2-3s | 1-1.5s | **50%** |
| Plan multi-tool (3 pasos) | 8-12s | 5-7s | **40%** |
| Error handler (con web) | 10-15s | 6-9s | **40%** |

---

## 🛠️ Hoja de Ruta de Implementación

### Fase 1 (Semana 1) — Quick Wins
1. ✅ Activar `num_gpu: 999` en `OLLAMA_GEN_OPTIONS`
2. ✅ Cambiar `OLLAMA_KEEP_ALIVE` a `"-1"`
3. ✅ Implementar ventana adaptativa de contexto
4. ✅ Caché de prompts de sistema

**Impacto acumulado:** 40-50% reducción de latencia.

### Fase 2 (Semana 2) — Optimizaciones Medias
5. ✅ Parallelizar búsqueda web + lectura de URLs
6. ✅ Pre-calentar grafo en background
7. ✅ Optimizar context manager (evitar doble construcción)

**Impacto acumulado:** 55-65% reducción de latencia.

### Fase 3 (Semana 3) — Refinamientos
8. ⚠️ Batch de múltiples órdenes (TUI only, evaluar UX)
9. ⚠️ Compresión de historial antiguo
10. ✅ Lazy load de tools pesadas

**Impacto acumulado:** 60-70% reducción de latencia.

---

## ⚠️ Consideraciones de Calidad

### NO Sacrificar
- ✅ **Validación de planes** (`validar_plan()` debe mantenerse)
- ✅ **Error handler** completo (diagnóstico + web + retry)
- ✅ **Contexto suficiente** para multi-tool (no recortar en pasos complejos)
- ✅ **Streaming** de tokens (UX crítica)

### Monitorizar
- 📊 **Tasa de error** después de optimizaciones (no debe aumentar)
- 📊 **Calidad de síntesis** en `node_plan_synthesizer` (evaluar con tests)
- 📊 **Uso de VRAM** con `num_gpu: 999` (ajustar si hay OOM)

---

## 🧪 Tests de Validación

Agregar tests específicos de latencia en `tests/`:

```python
# tests/test_latencia.py
import time

def test_latencia_charla_simple():
    start = time.perf_counter()
    result = procesar_orden_grafo("Hola, ¿cómo estás?", mem={})
    elapsed = time.perf_counter() - start
    assert elapsed < 0.8, f"Charla simple tardó {elapsed}s (esperado <0.8s)"

def test_latencia_busqueda_web():
    start = time.perf_counter()
    result = procesar_orden_grafo("¿Cuál es el precio de Bitcoin?", mem={})
    elapsed = time.perf_counter() - start
    assert elapsed < 4.0, f"Búsqueda web tardó {elapsed}s (esperado <4s)"
```

---

## 📝 Conclusión

Este plan prioriza **mejoras de alto impacto con bajo riesgo**, enfocándose en:
1. **Maximizar uso de GPU** (principal palanca de rendimiento)
2. **Reducir contexto innecesario** (prefill más rápido)
3. **Eliminar overhead repetitivo** (caché, lazy load)

La implementación gradual permite **medir impacto en cada fase** y revertir cambios si hay efectos negativos en la calidad.

**Meta final:** **60-70% menos latencia** manteniendo (o mejorando) la calidad de las respuestas.
