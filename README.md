# 🤖 Aether - Agente Local Inteligente (CLI)

Agente de IA basado en **LangGraph** + **Ollama** (Gemma 4:12B). Terminal-first, sin dependencias de web UI.

> Motor unificado de **Tool Planning**: el `planner` es la única puerta de decisión y siempre produce un plan (1 paso o multi-tool). Ver `TOOL_PLANNING.md` y `PLANNING_QUICKSTART.md`.

## 📁 Estructura del Proyecto

```
mi_proyecto_crew/
├── cli/                         # Interfaz CLI
│   └── main.py                  # Loop interactivo de terminal
├── backend/                     # FastAPI (opcional, para integración)
│   ├── api/
│   │   ├── main.py             # FastAPI app
│   │   ├── routes/
│   │   │   └── chat.py         # Endpoints
│   │   └── models/
│   │       └── schemas.py      # Pydantic models
│   ├── core/
│   │   ├── aether_service.py   # Servicio singleton
│   │   └── config.py           # Configuración
│   ├── requirements.txt
│   ├── .env
│   └── README.md
├── jarvis.py                   # Agente CrewAI + lógica de IA
├── run.py                      # Entrypoint principal
├── env/                        # Entorno virtual Python
└── .vscode/
    └── tasks.json              # Tasks para VS Code
```

## 🚀 Instalación Rápida

### Requisitos
- Python 3.10+
- Ollama corriendo en `http://localhost:11434`

### Setup Inicial

```bash
cd backend
pip install -r requirements.txt
```

## ▶️ Ejecutar Aether

### Opción 1: Ejecutar desde CLI (Recomendado)
```bash
python run.py
```

Verás:
```
🤖 [SISTEMA] Secuencia de inicio completada.
🎙️  Aether: Buenos días, Thomas. Matrices listas. Modo Autónomo: ACTIVO.

🧠 Creador: _
```

### Opción 2: VS Code Tasks
1. Abre la paleta de comandos: `Ctrl+Shift+P`
2. Escribe: "Run Task"
3. Selecciona: `Backend: Run FastAPI` (si deseas el API también)

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────┐
│     CLI (Terminal - Python stdin)           │
│  - Loop interactivo de entrada              │
│  - Salida en terminal                       │
│  - Manejo de comandos especiales            │
└──────────────┬──────────────────────────────┘
               │ Python import
               ↓
┌─────────────────────────────────────────────┐
│   Agente (core/ - LangGraph + Ollama)       │
│  - LocalLLM: Gemma 4:12B                    │
│  - Memory: SQLite (conversaciones)          │
│  - Tools: Sistema, búsqueda, visión, etc.   │
│  - 🆕 Tool Planning: Encadenamiento multi-tool │
│  - Ejecución autónoma/manual                │
└─────────────────────────────────────────────┘
```

### 🆕 Sistema de Tool Planning

Aether ahora puede encadenar múltiples herramientas automáticamente para resolver tareas complejas:

**Ejemplo:**
```
🧠 Creador: Busca el precio de Bitcoin y guárdalo en precio.txt

[El sistema automáticamente:]
1. 🔍 Busca en web el precio de Bitcoin
2. 💾 Crea archivo precio.txt con el resultado
3. 🎙️  Responde: "He guardado el precio de Bitcoin ($50,000) en precio.txt"
```

**Características:**
- ✅ Planificación inteligente con LLM
- ✅ Ejecución secuencial de pasos
- ✅ Contexto compartido entre herramientas
- ✅ Fallback seguro a flujo simple
- ✅ Compatible con todos los nodos existentes

📚 **Docs completas:** Ver `TOOL_PLANNING.md` y `PLANNING_QUICKSTART.md`

## 📖 Uso

Interactúa directamente con el agente:

```
🧠 Creador: ¿cuál es la capital de España?
🤖 [Javier PROCESANDO...]
🎙️  Javier: La capital de España es Madrid, ubicada en la región de la Comunidad de Madrid...

🧠 Creador: abre Firefox
🚀 [MEMORIA]: firefox conocido. Lanzando directamente...
🎙️  Javier: Firefox lanzado exitosamente.

🧠 Creador: salir
🤖 [SISTEMA] Desconectando sistemas. Hasta luego.
```

### Comandos Especiales
- `salir`, `adios`, `exit`, `quit` - Terminar sesión
- `mira`, `observa`, `captura` - Visión de pantalla
- `ejecuta`, `abre`, `lanza` - Ejecutar programas conocidos
- **lucide-react** - Iconos
- **Axios** - HTTP client
- **react-router-dom** - Enrutamiento
- **react-markdown** - Markdown rendering

## 🔧 Tecnologías Backend

- **FastAPI** - Framework web
- **Uvicorn** - ASGI server
- **Pydantic** - Data validation
- **Python-dotenv** - Env config
- **CORS** - Cross-origin requests

## 📝 Próximos Pasos

- [ ] Integrar `jarvis.py` en endpoints
- [ ] Persistencia en Base de Datos (SQLite/PostgreSQL)
- [ ] WebSocket para streaming de respuestas
- [ ] Sistema de autenticación
- [ ] Rate limiting
- [ ] Crear componentes UI base (Button, Input, Modal)
- [ ] Crear sub-componentes de chat
- [ ] Manejo de errores mejorado

## 🐛 Debugging

### Ver logs del backend
```bash
cd backend
python -m uvicorn api.main:app --reload --log-level debug
```

### Ver logs del frontend
```bash
cd frontend
npm run dev -- --debug
```

### Acceder a Swagger UI
```
http://localhost:8000/docs
```

## 📧 Contacto

Proyecto: Aether - Agente Local Inteligente
Base: CrewAI + Ollama + React

---

**Creado con ❤️ usando React, FastAPI y IA local**
