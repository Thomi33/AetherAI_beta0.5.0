from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import List

router = APIRouter(prefix="/api", tags=["chat"])

# In-memory storage (será reemplazado por DB real)
conversations_store = {}
conversation_id_counter = 1
message_id_counter = 1

class Message(BaseModel):
    content: str

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    id: int
    role: str
    content: str
    timestamp: str

@router.post("/chat")
async def send_message(request: ChatRequest):
    """
    Envía un mensaje al agente y recibe respuesta.
    Actualmente retorna mock response. TODO: Integrar con jarvis.py
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # Mock response - sin integración con jarvis.py todavía
    return {
        "response": "Aether backend online",
        "agent_status": "ready"
    }

@router.get("/conversations")
async def get_conversations():
    """
    Obtiene lista de conversaciones recientes.
    """
    try:
        conversations = [
            {
                "id": 1,
                "title": "Nueva conversación",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
        ]
        return conversations
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int):
    """
    Obtiene una conversación específica.
    """
    try:
        conversation = {
            "id": conversation_id,
            "title": f"Conversación {conversation_id}",
            "messages": [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        return conversation
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int):
    """
    Elimina una conversación.
    """
    try:
        return {"message": f"Conversación {conversation_id} eliminada"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/agent/status")
async def get_agent_status():
    """
    Obtiene estado simulado del agente.
    Sin acceso a Ollama ni SQLite.
    """
    return {
        "name": "Aether",
        "status": "ready",
        "model": "gemma4:12b",
        "version": "1.0",
        "uptime_seconds": 3600,
        "request_count": 42,
        "last_message": "Esperando comandos...",
        "backend_version": "1.0.0"
    }
