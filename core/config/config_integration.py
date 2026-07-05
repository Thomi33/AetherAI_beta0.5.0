"""
config_integration.py — Integración de ConfigManager con el motor LangGraph.

Este archivo conecta ConfigManager con los nodos del grafo LangGraph para
soportar hot reload de configuración sin reiniciar el proceso.

Cómo funciona:
  1. ConfigManager notifica cambios mediante EventBus
  2. Los nodos del grafo se suscriben a los cambios relevantes
  3. Cuando cambia una configuración, el nodo actualiza su estado interno

Configuraciones soportadas:
  - MODELO: Cambio de modelo LLM (requiere rebuild del LLM client)
  - TEMPERATURE: Cambio de temperatura (se pasa a ollama.chat)
  - MAX_TOKENS: Cambio de max_tokens (se pasa a ollama.chat)
  - NUM_CTX: Cambio de ventana de contexto (requiere rebuild del LLM client)
  - OLLAMA_HOST: Cambio de host Ollama (requiere rebuild del LLM client)
  - OLLAMA_KEEP_ALIVE: Cambio de keep_alive
  - OLLAMA_GEN_OPTIONS: Cambio de opciones de generación

Arquitectura:
  1. config_integration.py: Módulo de integración
  2. EventBus: Canal de comunicación
  3. NodeConfigAdapter: Clase que adapta nodos para接收 configuración

Uso:
    from core.config.config_integration import init_config_integration
    
    # Inicializar al iniciar el grafo
    init_config_integration()
    
    # Ahora los cambios en ConfigManager se propagan automáticamente
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional, Any

from core.config.config_manager import ConfigManager, get_config_manager, ConfigChangeEvent
from core.events import get_event_bus, Event, on_event
from langchain_core.language_models.base import BaseLanguageModel


class NodeConfigAdapter:
    """
    Adapta un nodo del grafo para que reciba configuración.
    
    Cada nodo define qué configuraciones necesita y cómo actualizarlas.
    """
    
    def __init__(self, node_name: str, config_keys: List[str], update_fn: Callable[[Dict[str, Any]], None]):
        self.node_name = node_name
        self.config_keys = config_keys
        self.update_fn = update_fn
        self._config: Dict[str, Any] = {}
        self._lock = threading.RLock()
    
    def update_config(self, config: ConfigManager) -> None:
        """Actualizar el config del nodo."""
        with self._lock:
            for key in self.config_keys:
                self._config[key] = config.get(key)
            self.update_fn(self._config)
    
    def get_config(self) -> Dict[str, Any]:
        """Obtener config actual del nodo."""
        with self._lock:
            return dict(self._config)


# Registry de adapters por nodo
_node_adapters: Dict[str, NodeConfigAdapter] = {}
_adapter_lock = threading.RLock()


def _create_llm_client_for_config(config: Dict[str, Any]) -> Any:
    """
    Crear un LLM client basado en la configuración.
    
    Esta función es específica de la implementación actual de Aether.
    Debe ajustarse según cómo se crea el LLM client en graph_nodes.py.
    """
    from langchain_ollama import ChatOllama
    
    model = config.get("MODELO", "ornith:9b")
    temperature = config.get("TEMPERATURE", 0.6)
    max_tokens = config.get("MAX_TOKENS", 2048)
    num_ctx = config.get("NUM_CTX", 8192)
    ollama_host = config.get("OLLAMA_HOST", "http://localhost:11434")
    
    return ChatOllama(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        num_ctx=num_ctx,
        base_url=ollama_host,
    )


def register_node_adapter(node_name: str, config_keys: List[str], update_fn: Callable[[Dict[str, Any]], None]) -> None:
    """
    Registrar un adapter para un nodo del grafo.
    
    Args:
        node_name: Nombre del nodo (ej: "planner", "node_text")
        config_keys: Lista de claves que el nodo necesita
        update_fn: Función que recibe config y actualiza el nodo
    """
    with _adapter_lock:
        adapter = NodeConfigAdapter(node_name, config_keys, update_fn)
        _node_adapters[node_name] = adapter


def _on_config_change(event: ConfigChangeEvent) -> None:
    """Handler para cambios de configuración."""
    with _adapter_lock:
        for adapter in _node_adapters.values():
            if event.key in adapter.config_keys:
                config = get_config_manager()
                adapter.update_config(config)


def init_config_integration() -> None:
    """Inicializar integración de ConfigManager con el grafo."""
    config = get_config_manager()
    
    # Suscribirse a todos los cambios de configuración
    config.subscribe("*", _on_config_change)
    
    # Actualizar todos los adapters con config inicial
    with _adapter_lock:
        for adapter in _node_adapters.values():
            adapter.update_config(config)


# Funciones de conveniencia para nodos específicos

def register_planner_adapter(update_fn: Callable[[Dict[str, Any]], None]) -> None:
    """Registrar adapter para planner."""
    register_node_adapter("planner", [
        "MODELO", "TEMPERATURE", "MAX_TOKENS", "NUM_CTX",
        "OLLAMA_HOST", "OLLAMA_KEEP_ALIVE"
    ], update_fn)


def register_text_node_adapter(update_fn: Callable[[Dict[str, Any]], None]) -> None:
    """Registrar adapter para node_text."""
    register_node_adapter("node_text", [
        "MODELO", "TEMPERATURE", "MAX_TOKENS", "NUM_CTX",
        "OLLAMA_HOST", "OLLAMA_KEEP_ALIVE"
    ], update_fn)


def register_synthesizer_adapter(update_fn: Callable[[Dict[str, Any]], None]) -> None:
    """Registrar adapter para node_plan_synthesizer."""
    register_node_adapter("node_plan_synthesizer", [
        "MODELO", "TEMPERATURE", "MAX_TOKENS", "NUM_CTX",
        "OLLAMA_HOST", "OLLAMA_KEEP_ALIVE"
    ], update_fn)
