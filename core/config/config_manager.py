"""
config_manager.py — Configuración centralizada con hot reload para Aether.

Responsabilidades:
  - Gestionar toda la configuración del sistema en un único lugar
  - Soportar recarga en caliente sin reiniciar el proceso
  - Validar configuraciones antes de aplicar cambios
  - Persistir en JSON/YAML (configurable)
  - Notificar cambios a suscriptores (event bus)

Arquitectura:
  1. ConfigManager: clase principal (singleton recomendado)
  2. ConfigValidator: validación de configuraciones
  3. ConfigWatcher: observador de archivos (inotify/watchdog)
  4. EventBus: notificación de cambios (opcional)

Uso:
    from core.config.config_manager import ConfigManager

    config = ConfigManager("config.json")

    # Leer
    modelo = config.get("MODELO")

    # Escribir (validado)
    config.set("MODELO", "nuevo_modelo:9b")

    # Suscribirse a cambios
    def on_change(key, value):
        print(f"Cambio: {key} = {value}")

    config.subscribe("MODELO", on_change)
"""

from __future__ import annotations

import json
import os
import re
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union


@dataclass
class ConfigChangeEvent:
    """Evento de cambio de configuración."""
    key: str
    value: Any
    old_value: Any
    timestamp: float


class ConfigValidator:
    """Validador de configuraciones."""
    
    VALID_MODELS = ["ornith:9b", "ornith:7b", "ornith-1.5:9b", "qwen3.5:9b",
                    "qwen2.5vl:7b", "qwen2.5:7b", "qwen2.5:3b", "qwen2.5:1.5b",
                    "moondream", "minicpm-v", "minicpm-v4.6:latest",
                    "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:q4_K_M",
                    "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M"]
    VALID_PROVIDERS = ["Ollama", "OpenAI", "Groq"]
    VALID_TEMP_RANGE = (0.0, 2.0)
    VALID_MAX_TOKENS_RANGE = (1, 8192)
    VALID_NUM_PREDICT_RANGE = (1, 65536)
    VALID_TIMEOUT_RANGE = (10, 300)
    VALID_CTX_RANGE = (4096, 262144)
    
    @staticmethod
    def validate_mode(value: Any) -> bool:
        return value in (True, False)
    
    @staticmethod
    def validate_timeout(value: Any) -> bool:
        try:
            val = int(value)
            return ConfigValidator.VALID_TIMEOUT_RANGE[0] <= val <= ConfigValidator.VALID_TIMEOUT_RANGE[1]
        except (ValueError, TypeError):
            return False
    
    @staticmethod
    def validate_num_ctx(value: Any) -> bool:
        try:
            val = int(value)
            return ConfigValidator.VALID_CTX_RANGE[0] <= val <= ConfigValidator.VALID_CTX_RANGE[1]
        except (ValueError, TypeError):
            return False
    
    @staticmethod
    def validate_temp(value: Any) -> bool:
        try:
            val = float(value)
            return ConfigValidator.VALID_TEMP_RANGE[0] <= val <= ConfigValidator.VALID_TEMP_RANGE[1]
        except (ValueError, TypeError):
            return False
    
    @staticmethod
    def validate_max_tokens(value: Any) -> bool:
        try:
            val = int(value)
            return ConfigValidator.VALID_MAX_TOKENS_RANGE[0] <= val <= ConfigValidator.VALID_MAX_TOKENS_RANGE[1]
        except (ValueError, TypeError):
            return False

    @staticmethod
    def validate_num_predict(value: Any) -> bool:
        """num_predict = presupuesto de tokens de generación (incluye el <think> de
        Ornith). Rango más amplio que MAX_TOKENS porque un effort alto necesita
        margen real para razonar sin cortarse a mitad del pensamiento."""
        try:
            val = int(value)
            return ConfigValidator.VALID_NUM_PREDICT_RANGE[0] <= val <= ConfigValidator.VALID_NUM_PREDICT_RANGE[1]
        except (ValueError, TypeError):
            return False
    
    @staticmethod
    def validate_model(value: Any) -> bool:
        # Acepta el catálogo + cualquier tag Ollama razonable (familia:variante),
        # porque el instalador elige modelo según RAM/VRAM de cada máquina.
        if value in ConfigValidator.VALID_MODELS:
            return True
        if isinstance(value, str) and re.match(r"^[a-zA-Z0-9][\w\-./]{1,80}(:[\w\-+.]{1,40})?$", value):
            return True
        return False
    
    @staticmethod
    def validate_provider(value: Any) -> bool:
        return value in ConfigValidator.VALID_PROVIDERS
    
    @staticmethod
    def validate_bool(value: Any) -> bool:
        return isinstance(value, bool)
    
    @staticmethod
    def validate_string(value: Any) -> bool:
        return isinstance(value, str) and len(value) > 0


class ConfigWatcher:
    """Observador de archivos de configuración (inotify o polling fallback)."""
    
    def __init__(self, config_path: Path, callback: Callable[[str], None]):
        self._config_path = config_path
        self._callback = callback
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_mtime = 0
        self._poll_interval = 2  # segundos
    
    def start(self) -> None:
        """Iniciar observación."""
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
    
    def stop(self) -> None:
        """Detener observación."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
    
    def _poll(self) -> None:
        """Polling de cambios en el archivo."""
        try:
            self._last_mtime = self._config_path.stat().st_mtime
        except FileNotFoundError:
            self._last_mtime = 0
        
        while self._running:
            try:
                current_mtime = self._config_path.stat().st_mtime
                if current_mtime != self._last_mtime:
                    self._last_mtime = current_mtime
                    self._callback(str(self._config_path))
            except FileNotFoundError:
                pass
            time.sleep(self._poll_interval)


class ConfigManager:
    """
    Gestor de configuración centralizado con hot reload y validación.
    """
    
    # Mapeo de validadores por clave
    VALIDATORS: Dict[str, Callable[[Any], bool]] = {
        "MODELO": ConfigValidator.validate_model,
        "MODELO_VISION": ConfigValidator.validate_model,
        "OLLAMA_HOST": ConfigValidator.validate_string,
        "SEARXNG_URL": ConfigValidator.validate_string,
        "MODO_AUTONOMO": ConfigValidator.validate_bool,
        "TOOL_CALLING_NATIVO": ConfigValidator.validate_bool,
        "TIMEOUT_CMD": ConfigValidator.validate_timeout,
        "NUM_CTX": ConfigValidator.validate_num_ctx,
        "MAX_TURNOS_CONTEXTO": ConfigValidator.validate_num_ctx,
        "MAX_TURNOS_CONTEXTO_PLAN": ConfigValidator.validate_num_ctx,
        "MAX_TURNOS_CONTEXTO_CHAT": ConfigValidator.validate_num_ctx,
        "OLLAMA_KEEP_ALIVE": ConfigValidator.validate_string,
        "OLLAMA_GEN_OPTIONS": lambda x: isinstance(x, dict),
        "OLLAMA_NUM_PARALLEL": lambda x: isinstance(x, int) and x > 0,
        "OLLAMA_MAX_LOADED_MODELS": lambda x: isinstance(x, int) and x > 0,
        "MAX_AGENT_STEPS": lambda x: isinstance(x, int) and 1 <= x <= 32,
        "MAX_STEPS_COMPUTER_USE": lambda x: isinstance(x, int) and 1 <= x <= 64,
        "MAX_HISTORIAL": lambda x: isinstance(x, int) and x > 0,
        "CONTEXTO_CONV_MAX_CHARS": lambda x: isinstance(x, int) and x > 0,
        "BASE_AETHER": ConfigValidator.validate_string,
        "CARPETA_AETHER": ConfigValidator.validate_string,
        "TEMPERATURE": ConfigValidator.validate_temp,
        "MAX_TOKENS": ConfigValidator.validate_max_tokens,
        "NUM_PREDICT": ConfigValidator.validate_num_predict,
        "NUM_PREDICT_PLANNER": ConfigValidator.validate_num_predict,
        "VERBOSE": ConfigValidator.validate_bool,
        "DEBUG": ConfigValidator.validate_bool,
        "THEME": ConfigValidator.validate_string,
        "REFRESH_RATE": lambda x: isinstance(x, (int, float)) and x > 0,
        "STT_ENABLED": ConfigValidator.validate_bool,
        "STT_LANGUAGE": lambda x: x is None or isinstance(x, str),
        "STT_VENV_PYTHON": lambda x: isinstance(x, str),
        "AUDIO_INPUT_MATCH": ConfigValidator.validate_string,
        "STT_VOCAB_HINT": lambda x: isinstance(x, str),
    }
    
    # Valores por defecto
    DEFAULTS: Dict[str, Any] = {
        "MODELO": "ornith:9b",
        "MODELO_VISION": "ornith-1.5:9b",
        "OLLAMA_HOST": "http://localhost:11434",
        "SEARXNG_URL": "http://localhost:8081",
        "MODO_AUTONOMO": True,
        "TOOL_CALLING_NATIVO": True,
        "TIMEOUT_CMD": 60,
        "NUM_CTX": 8192,
        "MAX_TURNOS_CONTEXTO": 200,
        "MAX_TURNOS_CONTEXTO_PLAN": 0,
        "MAX_TURNOS_CONTEXTO_CHAT": 10,
        "OLLAMA_KEEP_ALIVE": -1,
        "OLLAMA_GEN_OPTIONS": {"num_batch": 512, "num_gpu": 8, "num_thread": 8},
        "OLLAMA_NUM_PARALLEL": 4,
        "OLLAMA_MAX_LOADED_MODELS": 2,
        "MAX_AGENT_STEPS": 6,
        "MAX_STEPS_COMPUTER_USE": 8,
        "MAX_HISTORIAL": 100000,
        "CONTEXTO_CONV_MAX_CHARS": 16000,
        "BASE_AETHER": "~/Aether",
        "CARPETA_AETHER": "~/Aether",
        "TEMPERATURE": 0.6,
        "MAX_TOKENS": 2048,
        "NUM_PREDICT": 2048,
        # Piso de num_predict para llamadas de PLANIFICACIÓN (detección de
        # intención, plan multi-tool, resolución anafórica, args MCP). Estas
        # llamadas NO deben escalar con /effort: aunque el usuario esté en
        # effort 'low' (num_predict=768), el <think> nativo de Ornith puede
        # comerse ese presupuesto entero y cortar el JSON/decisión a mitad de
        # camino. Un plan truncado no es una respuesta 'más simple', es
        # inválida — por eso este piso es independiente del effort general.
        "NUM_PREDICT_PLANNER": 3072,
        "VERBOSE": False,
        "DEBUG": False,
        "THEME": "default",
        "REFRESH_RATE": 0.1,
        # Dictado por voz (F2 en la TUI). El modelo faster-whisper corre en
        # un venv aislado (~/whisper_aether_test/venv-stt) — ver
        # core/services/stt_service.py. STT_LANGUAGE fijo evita el costo de
        # autodetección y mejora precisión en code-switching es/en; "" =
        # autodetectar por turno.
        "STT_ENABLED": True,
        "STT_LANGUAGE": "es",
        "STT_VENV_PYTHON": "",
        # Nombre estable de la placa de entrada. Los índices ALSA/PyAudio
        # pueden cambiar tras un reinicio, por eso se resuelve en runtime.
        "AUDIO_INPUT_MATCH": "AudioBox USB 96",
        # Nombres propios/vocabulario técnico frecuente en las órdenes de
        # Thomas. Se pasa como initial_prompt a faster-whisper (condiciona
        # la decodificación): sin esto, whisper decodifica "Aether" o
        # "GitHub" como la palabra en español más parecida fonéticamente y
        # arrastra el error al resto de la frase. Editá esta lista con
        # /set STT_VOCAB_HINT "..." si aparecen palabras nuevas que
        # transcriben mal seguido.
        "STT_VOCAB_HINT": (
            "Aether, GitHub, Ollama, LangGraph, Ornith, MCP, Textual, "
            "Hyprland, Wayland, NVMe, Notion, Steam, Roblox, Minecraft, "
            "VLSM, subnetting, ydotool, faster-whisper, SQLite, "
            "consolidator, AudioBox USB 96, CrewAI."
        ),
    }
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Inicializar ConfigManager.
        
        Args:
            config_path: Ruta al archivo de configuración JSON.
                        Si no se pasa, se usa config.json en el directorio de config.
        """
        self._config_path = config_path or Path(__file__).parent / "config.json"
        self._config: Dict[str, Any] = {}
        self._subscriptions: Dict[str, List[Callable[[ConfigChangeEvent], None]]] = {}
        self._watcher: Optional[ConfigWatcher] = None
        self._lock = threading.RLock()
        
        # Cargar configuración inicial
        self._load_config()
    
    def _load_config(self) -> None:
        """Cargar configuración desde archivo, mergeando con defaults."""
        with self._lock:
            try:
                if self._config_path.exists():
                    with open(self._config_path, "r", encoding="utf-8") as f:
                        on_disk = json.load(f)
                    # Defaults + lo que hay en disco (disco tiene prioridad)
                    merged = dict(self.DEFAULTS)
                    merged.update(on_disk)
                    self._config = merged
                else:
                    self._config = dict(self.DEFAULTS)
            except (json.JSONDecodeError, IOError) as e:
                print(f"⚠️  [CONFIG]: Error cargando {self._config_path}: {e}")
                self._config = dict(self.DEFAULTS)
    
    def _save_config(self) -> None:
        """Guardar configuración en archivo."""
        with self._lock:
            try:
                # Crear directorio si no existe
                self._config_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(self._config_path, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, indent=2, ensure_ascii=False)
            except IOError as e:
                print(f"⚠️  [CONFIG]: Error guardando {self._config_path}: {e}")
    
    def _notify_subscribers(self, key: str, new_value: Any, old_value: Any) -> None:
        """Notificar cambios a suscriptores."""
        if key in self._subscriptions:
            event = ConfigChangeEvent(
                key=key, value=new_value, old_value=old_value, timestamp=time.time()
            )
            for handler in self._subscriptions[key]:
                try:
                    handler(event)
                except Exception as e:
                    print(f"⚠️  [CONFIG]: Error en subscriber de '{key}': {e}")
    
    # --- API pública ---
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Obtener valor de una configuración.
        
        Args:
            key: Nombre de la configuración
            default: Valor por defecto si no existe
        
        Returns:
            Valor de la configuración o default
        """
        with self._lock:
            return self._config.get(key, default)
    
    def get_all(self) -> Dict[str, Any]:
        """Obtener todas las configuraciones como dict."""
        with self._lock:
            return dict(self._config)
    
    def set(self, key: str, value: Any, validate: bool = True) -> bool:
        """
        Establecer valor de una configuración.
        
        Args:
            key: Nombre de la configuración
            value: Nuevo valor
            validate: Si True, validar antes de aplicar
        
        Returns:
            True si se aplicó, False si falló
        """
        with self._lock:
            # Validar si corresponde
            if validate and key in self.VALIDATORS:
                if not self.VALIDATORS[key](value):
                    print(f"❌ [CONFIG]: Validación falló para '{key}': {value}")
                    return False
            
            # Guardar valor anterior
            old_value = self._config.get(key)
            
            # Aplicar cambio
            self._config[key] = value
            
            # Guardar en archivo
            try:
                self._save_config()
                
                # Notificar suscriptores
                self._notify_subscribers(key, value, old_value)
                
                print(f"✅ [CONFIG]: {key} = {value}")
                return True
            except Exception as e:
                print(f"❌ [CONFIG]: Error aplicando cambio '{key}': {e}")
                # Revertir
                self._config[key] = old_value
                return False
    
    def set_multiple(self, config: Dict[str, Any], validate: bool = True) -> Dict[str, bool]:
        """
        Establecer múltiples configuraciones.
        
        Args:
            config: Dict con claves y valores
            validate: Si True, validar antes de aplicar
        
        Returns:
            Dict con resultados por clave
        """
        results = {}
        for key, value in config.items():
            results[key] = self.set(key, value, validate)
        return results
    
    def reload(self) -> bool:
        """
        Recargar configuración desde archivo (sin cambios).
        
        Returns:
            True si se recargó, False si falló
        """
        with self._lock:
            try:
                old_config = dict(self._config)
                self._load_config()
                
                # Detectar cambios
                for key in self._config:
                    if key in old_config and self._config[key] != old_config[key]:
                        self._notify_subscribers(
                            key, self._config[key], old_config[key]
                        )
                
                print(f"🔄 [CONFIG]: Recargado desde {self._config_path}")
                return True
            except Exception as e:
                print(f"❌ [CONFIG]: Error recargando: {e}")
                return False
    
    def reset(self, key: Optional[str] = None) -> bool:
        """
        Resetear configuración a valores por defecto.
        
        Args:
            key: Clave específica o None para todas
        
        Returns:
            True si se reseteó
        """
        with self._lock:
            if key:
                if key in self.DEFAULTS:
                    old_value = self._config.get(key)
                    self._config[key] = self.DEFAULTS[key]
                    self._save_config()
                    self._notify_subscribers(key, self.DEFAULTS[key], old_value)
                    print(f"🔄 [CONFIG]: {key} reseteado a {self.DEFAULTS[key]}")
                    return True
                return False
            
            # Resetear todo
            self._config = dict(self.DEFAULTS)
            self._save_config()
            print("🔄 [CONFIG]: Todas las configuraciones reseteadas")
            return True
    
    def subscribe(self, key: str, handler: Callable[[ConfigChangeEvent], None]) -> None:
        """
        Suscribirse a cambios de una configuración.
        
        Args:
            key: Clave a observar
            handler: Función que recibe ConfigChangeEvent
        """
        with self._lock:
            self._subscriptions.setdefault(key, []).append(handler)
    
    def unsubscribe(self, key: str, handler: Callable[[ConfigChangeEvent], None]) -> None:
        """Eliminar suscripción."""
        with self._lock:
            if key in self._subscriptions:
                try:
                    self._subscriptions[key].remove(handler)
                except ValueError:
                    pass
    
    def start_watching(self) -> None:
        """Iniciar observación de cambios en archivo."""
        if self._watcher is None:
            self._watcher = ConfigWatcher(
                self._config_path,
                lambda path: self.reload()
            )
            self._watcher.start()
    
    def stop_watching(self) -> None:
        """Detener observación de cambios en archivo."""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
    
    def save(self) -> bool:
        """Guardar configuración manualmente."""
        with self._lock:
            try:
                self._save_config()
                return True
            except Exception as e:
                print(f"❌ [CONFIG]: Error guardando: {e}")
                return False
    
    # --- Propiedades de conveniencia ---
    
    @property
    def modelo(self) -> str:
        """Propiedad para MODELO."""
        return self.get("MODELO", "ornith:9b")
    
    @modelo.setter
    def modelo(self, value: str) -> None:
        self.set("MODELO", value)
    
    @property
    def modo_autonomo(self) -> bool:
        """Propiedad para MODO_AUTONOMO."""
        return self.get("MODO_AUTONOMO", True)
    
    @modo_autonomo.setter
    def modo_autonomo(self, value: bool) -> None:
        self.set("MODO_AUTONOMO", value)
    
    @property
    def verbose(self) -> bool:
        """Propiedad para VERBOSE."""
        return self.get("VERBOSE", False)
    
    @verbose.setter
    def verbose(self, value: bool) -> None:
        self.set("VERBOSE", value)
    
    @property
    def debug(self) -> bool:
        """Propiedad para DEBUG."""
        return self.get("DEBUG", False)
    
    @debug.setter
    def debug(self, value: bool) -> None:
        self.set("DEBUG", value)
    
    @property
    def theme(self) -> str:
        """Propiedad para THEME."""
        return self.get("THEME", "default")
    
    @theme.setter
    def theme(self, value: str) -> None:
        self.set("THEME", value)
    
    @property
    def refresh_rate(self) -> float:
        """Propiedad para REFRESH_RATE."""
        return self.get("REFRESH_RATE", 0.1)
    
    @refresh_rate.setter
    def refresh_rate(self, value: float) -> None:
        self.set("REFRESH_RATE", value)


# Singleton de conveniencia
_config_manager: Optional[ConfigManager] = None
_config_manager_lock = threading.Lock()


def get_config_manager() -> ConfigManager:
    """Obtener el singleton de ConfigManager."""
    global _config_manager
    if _config_manager is None:
        with _config_manager_lock:
            if _config_manager is None:
                _config_manager = ConfigManager()
    return _config_manager


def reset_config_manager() -> None:
    """Resetear el singleton (útil para tests)."""
    global _config_manager
    with _config_manager_lock:
        _config_manager = None
