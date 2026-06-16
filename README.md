# 🤖 Aether - Agente Local Inteligente

Interfaz web elegante tipo **ChatGPT/OpenWebUI** para un agente local de IA basado en **CrewAI** + **Ollama** (Gemma 4:12B).

## 📁 Estructura del Proyecto

```
mi_proyecto_crew/
├── frontend/                    # React + Vite (Puerto 5173)
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/             # Componentes base
│   │   │   ├── chat/           # Componentes de chat
│   │   │   ├── sidebar/        # Sidebar.jsx ✅
│   │   │   └── header/         # Header.jsx ✅
│   │   ├── pages/
│   │   │   └── ChatPage.jsx    # ✅
│   │   ├── services/
│   │   │   └── api.js          # ✅
│   │   ├── context/
│   │   ├── hooks/
│   │   ├── utils/
│   │   │   └── constants.js    # ✅
│   │   ├── App.jsx             # ✅
│   │   └── main.jsx
│   ├── package.json
│   └── vite.config.js
│
├── backend/                     # FastAPI (Puerto 8000)
│   ├── api/
│   │   ├── main.py             # ✅ FastAPI app
│   │   ├── routes/
│   │   │   └── chat.py         # ✅ Endpoints
│   │   └── models/
│   │       └── schemas.py      # ✅ Pydantic models
│   ├── core/
│   │   └── config.py           # ✅ Settings
│   ├── requirements.txt        # ✅
│   ├── .env                    # ✅
│   └── README.md
│
├── jarvis.py                   # Agente CrewAI (ORIGINAL)
├── env/                        # Entorno virtual Python
├── start-aether.sh             # Script para iniciar todo
├── .vscode/
│   └── tasks.json              # Tasks para VS Code
└── manual_arch.md
```

## 🚀 Instalación Rápida

### Requisitos
- Python 3.10+
- Node.js 18+
- Ollama corriendo en `http://localhost:11434`

### Setup Inicial

#### 1. Backend
```bash
cd backend
pip install -r requirements.txt
```

#### 2. Frontend
```bash
cd frontend
npm install
```

## ▶️ Ejecutar Aether

### Opción 1: Script Automático (Recomendado)
```bash
chmod +x start-aether.sh
./start-aether.sh
```

Abre en navegador:
- **Frontend**: http://localhost:5173
- **API Docs**: http://localhost:8000/docs

### Opción 2: Manual (2 Terminales)

**Terminal 1 - Backend:**
```bash
cd backend
python -m uvicorn api.main:app --reload
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm run dev
```

### Opción 3: VS Code Tasks
1. Abre la paleta de comandos: `Ctrl+Shift+P`
2. Escribe: "Run Task"
3. Selecciona:
   - `Backend: Run FastAPI`
   - `Frontend: Run Dev Server`

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────┐
│          Frontend (React + Vite)            │
│  - ChatPage (UI estilo ChatGPT/OpenWebUI)   │
│  - Sidebar (Historial de conversaciones)    │
│  - Header (Dark mode, Menu)                 │
│  - Animations (Framer Motion)               │
└──────────────┬──────────────────────────────┘
               │ HTTP/JSON
               ↓
┌─────────────────────────────────────────────┐
│      Backend (FastAPI) - Puerto 8000        │
│  - GET /                    Info del API    │
│  - POST /api/chat           Enviar mensaje  │
│  - GET /api/conversations   Listar chats    │
│  - GET /api/agent/status    Estado agente  │
└──────────────┬──────────────────────────────┘
               │ IPC/Subprocess
               ↓
┌─────────────────────────────────────────────┐
│   Agente (jarvis.py - CrewAI + Ollama)      │
│  - LocalLLM: Gemma 4:12B                    │
│  - Memory: SQLite (conversaciones)          │
│  - Tools: Sistema, búsqueda, etc.           │
└─────────────────────────────────────────────┘
```

## 📋 API Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/` | Info del servidor |
| GET | `/health` | Health check |
| POST | `/api/chat` | Enviar mensaje al agente |
| GET | `/api/conversations` | Listar conversaciones |
| GET | `/api/conversations/{id}` | Obtener una conversación |
| DELETE | `/api/conversations/{id}` | Eliminar conversación |
| GET | `/api/agent/status` | Estado del agente |

## 🎨 Tecnologías Frontend

- **React 19** - UI
- **Vite** - Build tool
- **Tailwind CSS** - Estilos (con dark mode)
- **Framer Motion** - Animaciones
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
