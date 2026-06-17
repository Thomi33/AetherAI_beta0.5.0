from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json
import asyncio

from core.aether_service import AetherService

router = APIRouter()

@router.post("/chat")
async def chat(request: dict):
    message = request.get("message", "")

    async def stream_response():
        result = AetherService.process_message(message)
        response_text = result["response"]

        words = response_text.split()

        for word in words:
            yield f"data: {json.dumps({'token': word + ' '})}\n\n"
            await asyncio.sleep(0.03)

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )