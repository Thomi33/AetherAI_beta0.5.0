from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from core.config import settings
from core.aether_service import AetherService
from api.routes import chat

# =====================================================================
# LOGGING
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
)

logger = logging.getLogger(__name__)

# =====================================================================
# LIFESPAN
# =====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"🚀 Starting Aether API on {settings.API_HOST}:{settings.API_PORT}")
    logger.info(f"📡 CORS Origins: {settings.CORS_ORIGINS}")

    try:
        AetherService.initialize()
        logger.info("✅ Aether Agent inicializado exitosamente")
    except Exception as e:
        logger.warning(f"⚠️ Aether se inicializará on-demand: {e}")

    yield

    logger.info("👋 Shutting down Aether API")


# =====================================================================
# APP
# =====================================================================
app = FastAPI(
    title="Aether API",
    description="API para el agente local Aether",
    version="1.0.0",
    lifespan=lifespan
)

# =====================================================================
# CORS (FIX REAL PARA OPTIONS 400)
# =====================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # CLI only, permissive CORS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================
# ROUTERS
# =====================================================================
app.include_router(chat.router, prefix="/api")

# =====================================================================
# ROOT
# =====================================================================
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

# =====================================================================
# ERROR HANDLER
# =====================================================================
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)}
    )

# =====================================================================
# RUN
# =====================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.API_RELOAD
    )