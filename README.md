# 🤖 Aether — Agente Local Inteligente (CLI)

Aether (persona **"Javier"**) es un agente de IA **local y terminal-first** para
**Arch Linux**. Corre 100 % en tu máquina sobre **[Ollama](https://ollama.com)**
y se orquesta con **[LangGraph](https://langchain-ai.github.io/langgraph/)**.
Conversa, busca en la web, ejecuta comandos de shell, lanza aplicaciones, mira
la pantalla, genera y ejecuta código, gestiona su memoria y guarda archivos —
encadenando varias herramientas cuando la tarea lo requiere.

> **Motor único — Tool Planning.** El `planner` es la **única puerta de
> decisión**: siempre produce un *plan* (lista de pasos). Una tarea simple es
> un plan de 1 paso; una compleja es un plan multi-herramienta. Ver
> [`TOOL_PLANNING.md`](./TOOL_PLANNING.md) y
> [`PLANNING_QUICKSTART.md`](./PLANNING_QUICKSTART.md).

> ⚙️ **Sólo CLI.** Existe una carpeta `backend/` con una API FastAPI y un
> `frontend.tar.gz`, pero **no forman parte del flujo soportado**: el proyecto
> se usa desde la terminal. Esas piezas pueden ignorarse.

---

## 🧠 ¿Qué puede hacer? (Capacidades)

Aether expone **8 herramientas** (`TOOL_REGISTRY` en
`core/agent/tool_registry.py`). El planner elige una o varias por tarea:

| Tool | Qué hace | Implementación |
|---|---|---|
| `text` | Responde/conversa directamente con el LLM (streaming). | `node_text` |
| `web` | Busca en internet y sintetiza la respuesta. | `node_web` → `core/tools/web_search.py` + `url_reader.py` |
| `shell` | Genera y ejecuta un comando de sistema en **zsh** (con barreras de seguridad). | `node_shell` → `core/tools/shell_executor.py` |
| `launch` | Abre/lanza una aplicación (**Flatpak** o binario en el `PATH`). | `node_launch` → `core/tools/flatpak_manager.py` |
| `vision` | Captura la pantalla y la analiza con un modelo multimodal. | `node_vision` → `core/tools/vision.py` |
| `codigo` | Genera código (**python/bash**) y lo ejecuta. | `node_codigo` |
| `memory` | Gestiona la memoria del agente (ver/borrar/recordar); si no reconoce el pedido, responde como charla. | `node_memory` |
| `file_write` | Guarda el resultado de un paso anterior (o un texto) en un archivo. | `node_file_write` → `core/tools/file_writer.py` |

**Ejemplo de encadenamiento (multi-tool):**

```
🧠 Creador: busca el precio de Bitcoin y guárdalo en precio.txt

🧠 [PLANNER]: Decidiendo plan de ejecución...
   └─ Posible multi-tool, consultando LLM...
   └─ Plan multi-tool válido: 2 pasos
1. 🔍 web   → busca el precio de Bitcoin
2. 💾 file_write → guarda el resultado en ~/Aether/precio.txt
🎙️  Javier: Listo, guardé el precio de Bitcoin en ~/Aether/precio.txt
```

---

## 📁 Estructura del Proyecto

El corazón del agente vive en **`core/`**:

```
mi_proyecto_crew/
├── run.py                      # ▶️ Entrypoint principal (python run.py)
├── jarvis_new.py               # ▶️ Entrypoint alternativo (loop propio sobre el grafo)
├── test_planning.py            # 🧪 Runner de toda la suite tests/
│
├── cli/
│   └── main.py                 # Loop interactivo de terminal
│
├── core/                       # 🧠 Motor del agente (LangGraph + Ollama)
│   ├── agent/
│   │   ├── graph_builder.py    #   Construye y compila el grafo (get_graph)
│   │   ├── graph_nodes.py      #   Nodos: planner, intent gate, executor, tools, synthesizer, error handler
│   │   ├── graph_state.py      #   AetherState + crear_estado_inicial() (factory)
│   │   ├── tool_registry.py    #   TOOL_REGISTRY + validar_plan()
│   │   ├── prompts.py          #   Persona de ejecución ([SHELL]) + persona de charla (construir_persona_chat)
│   │   └── error_handler.py    #   Diagnóstico/reintento (legado; el handler activo está en graph_nodes)
│   ├── config/
│   │   └── settings.py         #   Modelos, hosts, rutas, límites de contexto
│   ├── memory/
│   │   ├── memory_manager.py   #   Memoria RAM + API pública (delega en store/)
│   │   ├── context_builder.py  #   Arma el contexto que se inyecta al prompt
│   │   └── store/              #   Subsistema controlado: current.db, esquema fijo,
│   │       │                   #   migraciones, write-API, guard, snapshots, staging
│   │       ├── schema.py  migrations/0001_init.sql  paths.py  connection.py
│   │       ├── guard.py  migrations.py  snapshots.py  store.py  legacy_import.py
│   ├── parser/
│   │   ├── shell_parser.py     #   Extrae [SHELL]...[/SHELL]
│   │   └── response_parser.py  #   Análisis de salida (LLM)
│   ├── tools/                  #   Implementación de cada herramienta
│   │   ├── web_search.py  url_reader.py  shell_executor.py
│   │   ├── flatpak_manager.py  vision.py  file_writer.py
│   ├── services/
│   │   └── graph_service.py    #   procesar_orden_grafo() — entrada única al motor
│   └── utils/intent_utils.py
│
├── tests/                      # 🧪 Suite de tests (no requieren Ollama; mockean LLM/tools)
│
└── backend/  frontend.tar.gz   # ⚙️ FastAPI/Front — fuera del flujo CLI (ignorar)
```

> El motor es **LangGraph puro** (sin CrewAI). Los entrypoints activos son
> `run.py` (recomendado) y `jarvis_new.py`.

---

## 🚀 Requisitos

**Software base**
- **Python 3.10+**
- **[Ollama](https://ollama.com)** corriendo en `http://localhost:11434`
- **zsh** (Aether ejecuta los comandos de sistema en `/bin/zsh`)

**Modelos de Ollama** (descargar con `ollama pull <modelo>`):
- Texto + razonamiento + tool calling → **`gemma4:e4b`**
- Visión (multimodal) → **`qwen3-vl:8b`**

(ambos configurables en `core/config/settings.py` → `MODELO` y `MODELO_VISION`).

**Dependencias del sistema según la herramienta**
| Para usar... | Necesitás |
|---|---|
| `vision` | **grim** (`pacman -S grim`) — pensado para **Hyprland/Wayland** (captura el monitor enfocado) |
| `launch` | **flatpak** y/o binarios en el `PATH` |
| `shell` / paquetes | **pacman** y **yay** (Arch Linux) |
| `web` (opcional) | una instancia **SearXNG** en `http://localhost:8081`; si no está, hay *fallback* automático a DuckDuckGo |

**Librerías Python** (el motor CLI usa, entre otras): `langgraph`,
`langchain-core`, `langchain-openai`, el cliente `ollama` y `requests`.

> Nota: `backend/requirements.txt` lista dependencias de **FastAPI**, que es la
> parte web opcional, no el motor CLI.

---

## ▶️ Ejecutar Aether

### Opción 1 — Entrypoint principal (recomendado)
```bash
python run.py
```

Verás algo como:
```
🤖 [SISTEMA] Secuencia de inicio completada.
🎙️  Aether: Buenos días, Thomas. Matrices listas (LangGraph). Modo Autónomo: ACTIVO.

🧠 Creador: _
```

### Opción 2 — Launcher alternativo
```bash
python jarvis_new.py
```
Hace lo mismo (precalienta el grafo y abre el loop interactivo), con su propio
`main()` que llama al motor directamente.

---

## 🏗️ Arquitectura y cómo funciona

### Cadena de arranque
```
python run.py
   └─► cli/main.py                       (loop de terminal)
         └─► backend/core/aether_service.py::AetherService   (singleton + memoria en RAM)
               └─► core/services/graph_service.py::procesar_orden_grafo()
                     └─► core/agent/graph_builder.py::get_graph()   (grafo LangGraph)
```

Cada orden del usuario se transforma en un `AetherState`
(`crear_estado_inicial()` en `core/agent/graph_state.py`, que **normaliza la
memoria** para evitar `KeyError`) y se ejecuta a través del grafo.

### Flujo del grafo (LangGraph)
```
START → planner → plan_executor (loop)
      → [ finalize (charla/narrativa) | plan_synthesizer → finalize (multi-tool o acción) ] → END

Si un paso marca error_activo:
  plan_executor → error_diagnose → error_confirm → error_retry
        → (éxito) reanuda el plan (executor / synthesizer / finalize)
        → (sigue fallando, hasta el límite) error_fallback
```

- **`planner`** (`node_planner`) — única decisión. Estrategia (en orden):
  0. **Fast-path de charla** (determinista, sin LLM): saludos, agradecimientos y
     small talk → plan de 1 paso `text` en *modo chat*.
  1. **Keywords deterministas** (prioridad `memory → vision → launch → web → codigo`).
     Si hay intención clara y la tarea **no** parece multi-tool → plan de 1 paso.
  2. Si parece encadenar acciones (un conector como *"y luego"*, *"y guarda"* + ≥2
     categorías de herramienta) → pide un **plan multi-tool al LLM** y lo valida con
     `validar_plan()`.
  3. **Intent gate (LLM)**: si no hubo keyword ni fast-path, el modelo **razona**
     si hace falta una herramienta o si es charla; *default* seguro a `text`. El
     planner **nunca rompe el flujo**.
- **`plan_executor`** (`node_plan_executor`) — ejecuta cada paso despachando al
  **nodo real** vía `TOOL_REGISTRY` (no reimplementa las tools). Acumula
  resultados, inyecta el contexto de pasos previos y captura errores por paso.
  Poda el historial según el caso: aislado en multi-tool, ventana chica en charla.
- **`plan_synthesizer`** (`node_plan_synthesizer`) — redacta la respuesta final
  con el modelo: en planes multi-paso sintetiza todos los resultados; en una
  **acción** de 1 paso (`launch`/`shell`/`codigo`/`file_write`) genera un cierre
  natural (qué se hizo, si salió bien y una frase amena, p.ej. *"¡que disfrutes la
  música!"*). Las tools narrativas (`text`/`web`/`vision`/`memory`) van directo a
  `finalize`.
- **`finalize`** (`node_finalize`) — registra la respuesta en la DB y marca `done`.
- **Error handler** — al detectar un error (shell/launch/codigo) diagnostica
  (incluso buscando en la web), propone un *fix*, pide confirmación, reintenta
  hasta un límite y, si todo falla, aplica una estrategia alternativa
  (`error_fallback`). Al resolver, **reanuda el plan**.

### Modelo "thinking"
`deepseek-r1:14b` separa su razonamiento (`thinking`) de la respuesta final. El
chat principal usa el cliente `ollama` en *streaming* (`core/agent/graph_nodes.py`)
con corte manual de bloques (p.ej. `[/SHELL]`) para no "alucinar" salidas.

### Charla vs. ejecución
El nodo `text` usa una **persona conversacional** (`construir_persona_chat` en
`prompts.py`) **sin** el protocolo `[SHELL]`, y recibe el historial **podado** a
`MAX_TURNOS_CONTEXTO_CHAT` turnos (anti-contaminación: que un *"Hola"* no derive
en una tarea vieja). Como red de seguridad, `node_text` corta/limpia cualquier
`[SHELL]` que el modelo igual intente emitir. Los nodos de **ejecución**
(shell/código/launch) siguen usando el backstory con protocolo `[SHELL]`
(`construir_backstory`).

---

## 🧩 Memoria

Aether mantiene memoria **en RAM** (durante la sesión) y **persistente en
SQLite** a través de un **subsistema controlado** (`core/memory/store/`),
diseñado para ser estable, migrable y resistente a corrupción.

- **DB única de producción:** `/mnt/basurero/Aether/db/current.db` (`DB_CURRENT`).
  Las demás DBs solo existen como `snapshots/`, `staging.db` y `backups/`.
- **Esquema FIJO y versionado** (migraciones en `store/migrations/NNNN_*.sql`):
  - `conversations` — `id, timestamp, content, source`
  - `memories` — `id, type (fact|preference|event), content, importance (0–10), created_at, tags`
  - `commands` — `id, command, result, timestamp`
- **Toda escritura pasa por la write-API** (`store.MemoryStore`): el agente
  NUNCA escribe SQLite directo. Flujo: *validar → normalizar → dedupe →
  escribir → snapshot si corresponde*.
- **Guard de integridad** (`store/guard.py`): rechaza columnas desconocidas o
  con nombres sospechosos (p.ej. `categorAether`) y bloquea cambios implícitos
  de esquema (drift). El esquema solo cambia con una migración versionada.
- **Snapshots inmutables + rollback** (`store/snapshots.py`): copia consistente
  read-only; se toma una antes de cada migración y de forma periódica.
- **Staging** (`staging.db`): para probar cambios antes de tocar producción.
- `memory_manager.py` expone la API de siempre (`registrar_turno`,
  `cargar_memoria`, `obtener_recuerdos`, `normalizar_mem`…) pero **delega toda la
  persistencia en `store/`**. El esquema del dict **en RAM** (garantizado por
  `normalizar_mem()`) se mantiene:
  ```python
  {
    "preferencias": {"nombre_usuario": str, "navegador": str, "notas": list},
    "flatpaks": dict,
    "historial_comandos": list,
    "conversacion": list,
  }
  ```

> La base vieja `memoria.db` (esquema `conversaciones/comandos/recuerdos`) quedó
> como **legacy de solo lectura**; sus datos se migraron a `current.db` con
> `store/legacy_import.py`.
- `core/memory/context_builder.py` arma el contexto que se inyecta en el prompt:
  perfil del usuario, recuerdos importantes, Flatpaks conocidos, últimos
  comandos y los turnos recientes de conversación (acotados por presupuesto de
  caracteres para no desbordar la ventana del modelo).

---

## 🔒 Seguridad de ejecución

El ejecutor de shell (`core/tools/shell_executor.py`) aplica barreras antes de
correr cualquier comando:

- **Bloquea comandos destructivos**: `rm -rf /`, `mkfs.*`, `dd if=`,
  `chmod` recursivo sobre `/`, *fork bombs* `:(){ }`, `kill -9 1`,
  escrituras a `/dev/sd*`, etc.
- **Prohíbe editores interactivos** (`nano`, `vim`, `vi`, `micro`, `emacs`)
  porque la terminal no es interactiva y se colgaría; sugiere `cat -n`, `sed -i`
  o scripts inline en su lugar.
- **Lanza apps GUI en segundo plano** (Flatpaks, Steam, navegadores, scripts
  `.py`, etc.) para no bloquear el loop.
- **Timeout** de `TIMEOUT_CMD = 60s` para comandos estándar.

Además, el *prompt* del sistema (`core/agent/prompts.py`) obliga al modelo a usar
el protocolo `[SHELL] <comando> [/SHELL]`, a **no inventar salidas** de la
terminal y a usar herramientas de Arch (`pacman`/`yay`/`flatpak`, nunca
`apt`/`dnf`/`snap`).

---

## ⚙️ Configuración (`core/config/settings.py`)

| Constante | Valor por defecto | Descripción |
|---|---|---|
| `MODELO` | `deepseek-r1:14b` | Modelo de texto + tool calling |
| `MODELO_VISION` | `qwen3-vl:8b` | Modelo multimodal para `vision` |
| `OLLAMA_HOST` | `http://localhost:11434` | Servidor Ollama |
| `SEARXNG_URL` | `http://localhost:8081` | Motor de búsqueda (fallback a DuckDuckGo) |
| `MODO_AUTONOMO` | `True` | Ejecuta sin pedir confirmación por cada paso |
| `TOOL_CALLING_NATIVO` | `True` | Tool calling estructurado nativo |
| `TIMEOUT_CMD` | `60` | Timeout (s) de comandos de shell |
| `NUM_CTX` | `16384` | Ventana de contexto del modelo |
| `MAX_TURNOS_CONTEXTO` | `200` | Máx. turnos de conversación inyectados |
| `MAX_TURNOS_CONTEXTO_PLAN` | `0` | Historial inyectado durante pasos de un plan multi-tool (0 = aislado) |
| `MAX_TURNOS_CONTEXTO_CHAT` | `10` | Historial inyectado en el camino de charla (ventana chica anti-contaminación) |
| `BASE_AETHER` | `/mnt/basurero/Aether` | Carpeta base del agente (DB, logs, screenshots…). |
| `CARPETA_AETHER` | `~/Aether` | Destino por defecto de los archivos que crea `file_write` |
| `OLLAMA_KEEP_ALIVE` | `30m` | Mantiene el modelo cargado en VRAM entre llamadas (↓ latencia) |
| `OLLAMA_GEN_OPTIONS` | `{num_batch: 512}` | Opciones de generación extra (throughput); `num_gpu`/`num_thread` opcionales para forzar GPU |

---

## 📖 Uso e interacción

```
🧠 Creador: ¿cuál es la capital de España?
🎙️  Javier: La capital de España es Madrid...

🧠 Creador: abre Firefox
🚀 [Aether]: Lanzando Firefox...
🎙️  Javier: Firefox lanzado.

🧠 Creador: ¿qué ves en la pantalla?
🎙️  Javier: Veo una terminal con...

🧠 Creador: salir
🤖 [SISTEMA] Desconectando sistemas. Hasta luego.
```

### Palabras para terminar la sesión
`salir`, `adios`, `exit`, `apágate`, `quit`

> No hay "comandos especiales" fijos para activar herramientas: **el planner
> decide** la(s) tool(s) a partir del lenguaje natural de tu orden.

---

## 🧪 Tests

La suite vive en `tests/` y **no requiere Ollama** (mockean LLM/tools y usan
memoria normalizada). Se ejecutan con el runner agregado:

```bash
python test_planning.py            # corre TODOS los tests/test_*.py y reporta un resumen
```

También se pueden correr de forma individual, por ejemplo:
```bash
python tests/test_planner.py            # decisión del planner
python tests/test_tool_registry.py      # registro de tools + validar_plan()
python tests/test_executor.py           # ejecución de pasos
python tests/test_integracion.py        # end-to-end con el grafo real (mocks)
python tests/test_planning_e2e.py       # planning multi-tool end-to-end
python tests/test_shell_stop.py         # corte de generación de shell
python tests/test_filewrite_flow.py     # flujo de file_write
```

Verificación rápida de que el grafo compila:
```bash
python -c "from core.agent.graph_builder import build_graph; build_graph()"
```

---

## 📜 Notas de migración (CrewAI → LangGraph)

- El motor se **migró de CrewAI a LangGraph** y **CrewAI fue eliminado por
  completo**: ya no queda código ni dependencia de `crewai`. Se borraron
  `builder.py`, `executor.py`, el servicio legado y los launchers `jarvis*.py`,
  y se **desinstaló el paquete `crewai`** del entorno (`crewai-env`).
- La orquestación vive en `core/agent/` y se invoca por
  `core/services/graph_service.py` (`procesar_orden_grafo`). El cliente de LLM es
  el **`ollama` nativo** (más `langchain-*` en módulos auxiliares, que pasan la
  key explícitamente; no dependen de variables de entorno de CrewAI/LiteLLM).
- `node_router` se conserva como helper **deprecado**: ya no está cableado en el
  grafo; su lógica de keywords la usa ahora el `planner`
  (`_detectar_intent_keywords`).
- Detalle histórico del refactor en [`REFACTORING_REPORT.md`](./REFACTORING_REPORT.md).

> Si alguna vez necesitaras volver a CrewAI, reinstalá con `pip install
> crewai==1.14.7` — pero el flujo soportado es **LangGraph-only**.

---

**Aether — IA local, privada y bajo tu control. Hecho para la terminal.**
