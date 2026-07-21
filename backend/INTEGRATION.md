# 🔗 Integración Aether + FastAPI

## Arquitectura

```
Frontend (Axios)
    ↓
POST /api/chat
    ↓
FastAPI Endpoint
    ↓
AetherService.process_message()
    ↓
AetherService.process_message()
    ↓
graph_service.procesar_orden_grafo()
    ↓
ornith:9b (Ollama)
    ↓
Respuesta → Frontend
```

## AetherService (Singleton)

**Archivo**: `backend/core/aether_service.py`

**Características**:
- ✅ Inicializa el grafo de LangGraph UNA SOLA VEZ
- ✅ Mantiene memoria en RAM entre requests
- ✅ Thread-safe: `threading.Lock` protege la ejecución
- ✅ Logging interno (sin exponer errores al frontend)
- ✅ Sin modificar el motor CLI principal

**Flujo**:
```python
1. AetherService.initialize()
   - Importa core.services.graph_service
   - Ejecuta: inicializar_db()
   - Carga: cargar_memoria()
   - Indexa: actualizar_programas()

2. AetherService.process_message(user_msg)
   - Lock: _AETHER_LOCK.acquire()
   - Registra: registrar_turno(user)
   - Procesa: procesar_orden_grafo()
   - Registra: registrar_turno(respuesta)
   - Unlock
```

## Endpoints

### POST /api/chat
```json
Entrada:
{
  "message": "¿Cuál es el clima hoy?"
}

Salida:
{
  "response": "[respuesta del agente]",
  "agent_status": "ready|error"
}
```

### GET /api/agent/status
```json
{
  "name": "Aether",
  "status": "ready|error|uninitialized",
  "model": "ornith:9b",
  "version": "1.0.0",
  "memory_size": 42,
  "agent_status": "online|offline"
}
```

## Base de Datos

**Ruta**: `/mnt/nvme/Aether/db/current.db`

**Tablas SQLite**:
- `conversations` - Turnos usuario/agente
- `commands` - Historial de comandos ejecutados
- `memories` - Preferencias y contexto

**WAL Mode**: Soporta múltiples readers + 1 writer simultaneamente

## Thread Safety

```python
_AETHER_LOCK = threading.Lock()

# Cada request adquiere el lock antes de acceder a:
with _AETHER_LOCK:
    registrar_turno()           # → SQLite
    procesar_orden_grafo()      # → LLM + Grafo LangGraph
```

## Logging

**Nivel**: DEBUG (por defecto INFO)

**Ubicación**: Logs en stderr del servidor

**Logs especiales**:
- ✅ `"✅ AetherService inicializado exitosamente"`
- ✅ `"✅ Aether inicializado correctamente"`
- ⚠️ `"❌ Error durante inicialización de Aether: ..."`
- ⚠️ `"Error procesando mensaje: ..."` (sin str(e) al frontend)

## Testing

```bash
# 1. Iniciar backend
cd backend
python -m uvicorn api.main:app --reload

# 2. Verificar health
curl http://localhost:8000/health

# 3. Ver Swagger UI
open http://localhost:8000/docs

# 4. Enviar mensaje
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hola Aether"}'

# 5. Verificar estado
curl http://localhost:8000/api/agent/status
```

## Reversibilidad

Si necesitas desactivar la integración:

1. Cambiar en `backend/api/routes/chat.py`:
```python
# Revertir a mock
result = {
    "response": "Mock response",
    "agent_status": "ready"
}
return result
```

2. No necesita modificar el motor core
3. No afecta BD SQLite
4. Solo eliminar `backend/core/aether_service.py` si se desea

---

**Estado**: ✅ **INTEGRACIÓN COMPLETA Y FUNCIONAL**
