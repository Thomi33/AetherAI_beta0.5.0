# Aether

> Agente local, terminal-first y en español rioplatense.
> **LangGraph + Ollama + herramientas reales + memoria persistente.**

Aether es un asistente local que conversa, analiza proyectos, busca información
actualizada, lee y escribe archivos, ejecuta comandos, genera código, observa
la pantalla y encadena varias acciones sin enviar el proyecto a servicios
externos. El modelo corre en tu máquina mediante Ollama.

## Índice

- [Instalación](#instalación)
- [Uso](#uso)
- [Cómo funciona](#cómo-funciona)
- [Herramientas](#herramientas)
- [Configuración y rendimiento](#configuración-y-rendimiento)
- [Memoria y datos](#memoria-y-datos)
- [Arquitectura del proyecto](#arquitectura-del-proyecto)
- [Desarrollo y pruebas](#desarrollo-y-pruebas)
- [Solución de problemas](#solución-de-problemas)

## Instalación

### Requisitos

- Linux (el instalador está optimizado para Arch Linux).
- Python 3.10 o superior.
- [Ollama](https://ollama.com) ejecutándose en `http://localhost:11434`.
- `zsh` para las herramientas de shell.
- `grim` solo si vas a usar visión en Hyprland/Wayland.

### Instalación recomendada

Desde la raíz del repositorio:

```bash
chmod +x install.sh
./install.sh
```

El instalador:

1. Detecta CPU, RAM, VRAM y espacio disponible.
2. Recomienda un modelo y parámetros adecuados al equipo.
3. Duplica el contexto base recomendado, con un máximo de `65536`.
4. Descarga el modelo principal si lo autorizás.
5. Configura el entorno sin eliminar funcionalidades opcionales.

La visión usa el mismo modelo principal: Ornith 1.5 con soporte multimodal.
No se descarga un segundo modelo de visión.

### Instalación manual

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ollama pull ornith-1.5:9b
```

Si tu hardware necesita otro modelo, podés indicarlo al instalador o editar
`core/config/config.json`.

## Uso

### TUI principal

```bash
python run.py
```

### Lanzador global

El instalador puede crear el comando `aether` en `~/.local/bin`:

```bash
aether
aether task "listá los archivos del proyecto"
aether cli
aether doctor
aether --version
```

También podés ejecutar Aether sobre un directorio específico:

```bash
cd ~/Proyecto
aether
aether task "creá un README para este proyecto"
aether --workdir ~/Proyecto task "ejecutá los tests"
```

Antes de operar en un directorio nuevo, Aether solicita autorización. Las
denegaciones no se guardan: si volvés a iniciar Aether, volverá a preguntar.

### Detener una inferencia

Durante una operación podés usar cualquiera de estas opciones:

- Botón **Detener** en la TUI.
- `Ctrl+C`.
- Comando `/stop`.

La cancelación es cooperativa: detiene el flujo cuando Ollama entrega el
siguiente evento disponible.

## Cómo funciona

Cada pedido se convierte en un estado `AetherState` y atraviesa un grafo
LangGraph:

```text
START
  -> planner
  -> context_manager
  -> plan_executor o agent_loop
  -> plan_synthesizer/finalize
  -> END
```

- **Planner:** resuelve fast-paths simples y prepara planes de uno o varios
  pasos.
- **Agent loop:** usa tool calling nativo de Ollama para tareas abiertas.
- **Executor:** despacha cada paso a la implementación real de la herramienta.
- **Synthesizer:** combina resultados cuando hay varias acciones.
- **Error handler:** diagnostica errores, puede buscar una solución, reintenta
  dentro de límites y deja el error explícito si no se resuelve.
- **Context manager:** poda y compacta contexto para evitar consumo innecesario
  de VRAM.

Si Aether no tiene certeza, el prompt le indica buscar primero en internet
mediante `web` cuando el dato sea actual, versionado o verificable externamente.
Para hechos locales debe preferir archivos, shell y herramientas del sistema.

La TUI muestra estados reales de ejecución, por ejemplo:

```text
[•] Analizando solicitud...
[•] Preparando plan...
[•] Ejecutando herramienta: shell
aptretando contexto pa salvar tu VRAM...
[✓] Operación completada
```

Los mensajes de personalidad son opcionales, breves y secundarios; no
reemplazan estados reales ni inventan acciones.

## Herramientas

| Herramienta | Función |
|---|---|
| `text` | Conversación directa con streaming. |
| `web` | Búsqueda y lectura de información actual. |
| `shell` | Ejecuta comandos `zsh` con barreras y timeout. |
| `launch` | Abre aplicaciones y paquetes Flatpak. |
| `vision` | Captura y analiza la pantalla con el modelo principal multimodal. |
| `codigo` | Genera, guarda y ejecuta Python, Bash o Java. |
| `memory` | Consulta, guarda o elimina recuerdos. |
| `file_write` | Guarda el resultado de un paso en un archivo. |
| `fs_read` / `fs_write` | Lee y escribe archivos con rutas explícitas. |
| `fs_mkdir` / `fs_list` | Crea directorios y lista contenido. |
| `computer_use` | Controla mouse/teclado cuando está disponible. |
| `mcp` | Invoca servidores MCP configurados. |

Las rutas relativas se resuelven en el directorio de trabajo autorizado, no en
la carpeta del repositorio. Por defecto la memoria, notas y logs viven aparte
en `~/Aether`; si durante el onboarding se elige no usar un home separado,
viven en `<proyecto>/.aether-data/`.

## Configuración y rendimiento

La configuración principal está en [`core/config/config.json`](./core/config/config.json).
Ese JSON es la fuente única de valores configurables. `settings.py` se conserva
como adaptador de compatibilidad para rutas calculadas y módulos antiguos; no
debe contener valores alternativos de configuración.
Los valores más importantes son:

- `MODELO`: modelo principal para texto, tool calling y visión.
- `AETHER_DATA_DIR`: carpeta donde se guardan memoria, notas y logs.
- `NUM_CTX`: contexto efectivo; el instalador lo ajusta según hardware.
- `NUM_PREDICT`: máximo de tokens generados.
- `MAX_AGENT_STEPS`: límite de iteraciones del agente.
- `OLLAMA_KEEP_ALIVE`, threads y batch: parámetros de latencia y memoria.
- `STT_ENABLED`: dictado por voz opcional.

Los valores editados manualmente tienen prioridad sobre los defaults del
programa. Un contexto más grande no siempre es más rápido: depende de la RAM,
VRAM, cuantización y modelo elegido.

## Memoria y datos

Aether mantiene memoria temporal en RAM y persistente mediante el subsistema
controlado de [`core/memory/store`](./core/memory/store):

- Producción: `$AETHER_DATA_DIR/db/current.db`.
- Pruebas: `$AETHER_DATA_DIR/db/staging.db`.
- Snapshots: `$AETHER_DATA_DIR/db/snapshots/`.
- Backups: `$AETHER_DATA_DIR/db/backups/`.

Las escrituras pasan por `MemoryStore`, migraciones versionadas y un guard de
integridad. La base `memoria.db` que estaba en la raíz era un artefacto legacy
sin uso por el runtime actual; fue conservada fuera del árbol principal en
`_legacy/data/` junto con el dump antiguo para no perder datos históricos.

Durante inferencias largas, el resumen consolidado también se guarda en
`notes/contexto_importante.md` antes de compactar el contexto. Así los datos
importantes sobreviven aunque el prompt se reduzca. Durante el onboarding, el
instalador pregunta si Aether debe tener su propia carpeta. Si elegís que no,
usa `<proyecto>/.aether-data/` para la base y sus notas.

## Arquitectura del proyecto

```text
.
├── run.py                    # Entrada principal
├── install.sh                # Instalador y perfilado de hardware
├── bin/                      # Lanzador global aether
├── core/
│   ├── agent/                # Grafo, planner, loop y tools
│   ├── config/               # Configuración y autorización de directorios
│   ├── memory/               # Memoria, compactación y SQLite
│   ├── services/             # Entrada única al grafo
│   └── tools/                # Shell, web, visión y filesystem
├── tui/                      # Interfaz Textual y estados
├── skills/                   # Instrucciones reutilizables
├── tests/                    # Tests automatizados
└── backend/                  # API FastAPI opcional
```

Las skills operativas viven en `skills/<nombre>/SKILL.md`. Se mantienen como
archivos separados porque Aether los descubre y carga dinámicamente; no son
documentación redundante.

## Backend opcional

El flujo soportado es la TUI/CLI, pero existe una API FastAPI experimental:

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Endpoints principales: `GET /health`, `POST /api/chat`,
`GET /api/conversations` y `GET /api/agent/status`. Swagger queda disponible
en `http://localhost:8000/docs`.

## Desarrollo y pruebas

Activá el entorno y ejecutá los tests existentes:

```bash
source .venv/bin/activate
python -m pytest tests
python -m py_compile run.py tui/app.py core/agent/graph_nodes.py
```

Para comprobar que el grafo compila:

```bash
python -c "from core.agent.graph_builder import build_graph; build_graph(); print('ok')"
```

El script de auditoría está en [`tools/audit_project.py`](./tools/audit_project.py)
y sirve como diagnóstico manual; no forma parte del arranque normal.

## Solución de problemas

### Ollama no responde

```bash
ollama serve
ollama list
ollama pull ornith-1.5:9b
```

### El modelo responde lento

Reducí `NUM_CTX` o `NUM_PREDICT`, comprobá la VRAM disponible y verificá que
Ollama esté usando GPU. El hardware y el modelo son los límites principales;
Aether evita trabajo redundante, compacta contexto y limita loops, pero no
puede acelerar la generación intrínseca del modelo.

### Directorio rechazado

El rechazo cierra esa ejecución y no queda persistido. Iniciá Aether nuevamente
para autorizarlo cuando quieras.

### Visión no funciona

Verificá `grim`, Wayland/Hyprland y que el modelo configurado soporte imágenes:

```bash
command -v grim
ollama show "$(python -c 'import json; print(json.load(open("core/config/config.json"))["MODELO"])')"
```

## Licencia y estado

Proyecto en desarrollo activo. Revisá los cambios directamente en Git y no
comprometas credenciales, archivos `.env`, bases de datos personales ni
configuraciones locales.
