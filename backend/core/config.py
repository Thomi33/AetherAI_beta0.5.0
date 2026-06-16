from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_RELOAD: bool = True
    
    # CORS
    CORS_ORIGINS: list = ["http://localhost:5173", "http://localhost:3000"]
    
    # Agent
    AGENT_NAME: str = "Aether"
    AGENT_MODEL: str = "gemma4:12b"
    OLLAMA_HOST: str = "http://localhost:11434"
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    AGENT_SCRIPT: str = str(BASE_DIR / "jarvis.py")
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
