from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from core.config import settings
from api.routes import chat

# Lifespan context
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print(f"🚀 Starting Aether API on {settings.API_HOST}:{settings.API_PORT}")
    print(f"📡 CORS Origins: {settings.CORS_ORIGINS}")
    yield
    # Shutdown
    print("👋 Shutting down Aether API")

# Create FastAPI app
app = FastAPI(
    title="Aether API",
    description="API para el agente local Aether",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router)

@app.get("/")
async def root():
    return {
        "name": "Aether",
        "version": "1.0.0",
        "status": "online",
        "docs": "/docs"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "agent": "Aether",
        "ollama": settings.OLLAMA_HOST
    }

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.API_RELOAD
    )
