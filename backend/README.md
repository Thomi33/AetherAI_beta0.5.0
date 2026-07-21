# Aether Backend - FastAPI

API REST para el agente local Aether (LangGraph + Ollama)

## Instalación

```bash
cd backend
pip install -r requirements.txt
```

## Ejecutar

```bash
# Desde la carpeta backend
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# O directamente
python api/main.py
```

## Endpoints

- `GET /` - Info del servidor
- `GET /health` - Health check
- `POST /api/chat` - Enviar mensaje al agente
- `GET /api/conversations` - Obtener conversaciones
- `GET /api/conversations/{id}` - Obtener conversación específica
- `DELETE /api/conversations/{id}` - Eliminar conversación
- `GET /api/agent/status` - Estado del agente

## Docs

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## TODO

- [ ] Integrar nuevo loop LangGraph
- [ ] Persistencia en DB
- [ ] WebSocket para streaming
- [ ] Autenticación
- [ ] Rate limiting
