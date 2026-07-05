"""
events.py — Event Bus sistémico para Aether.

Responsabilidades:
  - Desacoplar componentes mediante pub/sub
  - Centralizar logs y métricas
  - Facilitar debugging y extensiones
  - Soportar eventos asíncronos y síncronos

Uso:
    from core.events import EventBus, Event

    bus = EventBus()

    # Suscribirse a eventos
    def on_user_command(payload):
        print(f"Comando: {payload['command']}")

    bus.subscribe("user_command", on_user_command)

    # Publicar eventos
    bus.publish(Event("user_command", {"command": "busca el clima"}))
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar


@dataclass
class Event:
    """
    Evento del bus.
    
    Args:
        type: Tipo de evento (ej: "user_command", "tool_executed")
        payload: Datos del evento (dict)
        timestamp: Marcador de tiempo (automático si no se pasa)
        source: Origen del evento (opcional)
    """
    type: str
    payload: Dict[str, Any]
    timestamp: float = None
    source: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().timestamp()


class EventHandler:
    """Manejador de eventos con posibilidad de filtro."""
    
    def __init__(
        self,
        callback: Callable[[Event], None],
        filter_type: Optional[str] = None,
        filter_fn: Optional[Callable[[Event], bool]] = None
    ):
        self.callback = callback
        self.filter_type = filter_type
        self.filter_fn = filter_fn
    
    def matches(self, event: Event) -> bool:
        """Verificar si este handler debe procesar el evento."""
        if self.filter_type and event.type != self.filter_type:
            return False
        if self.filter_fn and not self.filter_fn(event):
            return False
        return True
    
    def handle(self, event: Event) -> None:
        """Ejecutar callback si el evento coincide."""
        if self.matches(event):
            try:
                self.callback(event)
            except Exception as e:
                print(f"⚠️  [EVENTS]: Error en handler de '{event.type}': {e}")


class EventBus:
    """
    Event Bus para desacoplamiento de componentes.
    
    Características:
      - Pub/Sub (publicar/suscribir)
      - Filtros por tipo y función personalizada
      - Manejo asíncrono y síncrono
      - Logging centralizado
    """
    
    def __init__(self, verbose: bool = False):
        self._handlers: List[EventHandler] = []
        self._verbose = verbose
        self._lock = threading.RLock()
        self._event_history: List[Event] = []
        self._max_history = 100
    
    def publish(self, event: Event) -> int:
        """
        Publicar un evento a todos los suscriptores.
        
        Args:
            event: Evento a publicar
        
        Returns:
            Cantidad de handlers que procesaron el evento
        """
        with self._lock:
            # Guardar en historial
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history.pop(0)
            
            # Notificar
            count = 0
            for handler in self._handlers:
                if handler.matches(event):
                    if self._verbose:
                        print(f"📢 [EVENTS]: {event.type} → {handler.callback.__name__}")
                    handler.handle(event)
                    count += 1
            
            return count
    
    def subscribe(
        self,
        callback: Callable[[Event], None],
        type: Optional[str] = None,
        filter_fn: Optional[Callable[[Event], bool]] = None
    ) -> Callable[[Event], None]:
        """
        Suscribirse a eventos.
        
        Args:
            callback: Función que recibe Event
            type: Tipo de evento específico (opcional)
            filter_fn: Función de filtro personalizada (opcional)
        
        Returns:
            El mismo callback para uso en desuscripción
        """
        with self._lock:
            handler = EventHandler(callback, type, filter_fn)
            self._handlers.append(handler)
            return callback
    
    def unsubscribe(self, callback: Callable[[Event], None]) -> bool:
        """
        Eliminar suscripción.
        
        Args:
            callback: Función a eliminar
        
        Returns:
            True si se encontró y eliminó
        """
        with self._lock:
            for i, handler in enumerate(self._handlers):
                if handler.callback == callback:
                    self._handlers.pop(i)
                    return True
            return False
    
    def clear(self) -> None:
        """Eliminar todos los handlers."""
        with self._lock:
            self._handlers.clear()
    
    def get_history(self, type: Optional[str] = None, limit: int = 50) -> List[Event]:
        """
        Obtener historial de eventos.
        
        Args:
            type: Filtrar por tipo (opcional)
            limit: Máximo de eventos
        
        Returns:
            Lista de eventos
        """
        with self._lock:
            events = self._event_history
            if type:
                events = [e for e in events if e.type == type]
            return events[-limit:]
    
    def clear_history(self) -> None:
        """Limpiar historial de eventos."""
        with self._lock:
            self._event_history.clear()


# Singleton global
_global_event_bus: Optional[EventBus] = None
_global_event_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Obtener el EventBus global."""
    global _global_event_bus
    if _global_event_bus is None:
        with _global_event_bus_lock:
            if _global_event_bus is None:
                _global_event_bus = EventBus(verbose=False)
    return _global_event_bus


def reset_event_bus() -> None:
    """Resetear EventBus global (útil para tests)."""
    global _global_event_bus
    with _global_event_bus_lock:
        _global_event_bus = None


# Decoradores de conveniencia

def on_event(event_type: str):
    """
    Decorador para suscribir funciones a eventos.
    
    Uso:
        @on_event("user_command")
        def on_command(event):
            print(f"Comando: {event.payload['command']}")
    """
    def decorator(callback: Callable[[Event], None]) -> Callable[[Event], None]:
        bus = get_event_bus()
        bus.subscribe(callback, type=event_type)
        return callback
    return decorator


def async_on_event(event_type: str):
    """
    Decorador para eventos asíncronos.
    
    Uso:
        @async_on_event("user_command")
        async def on_command(event):
            await process_async(event.payload['command'])
    """
    def decorator(callback: Callable[[Event], Any]) -> Callable[[Event], Any]:
        bus = get_event_bus()
        
        @wraps(callback)
        def wrapper(event: Event):
            asyncio.create_task(callback(event))
        
        bus.subscribe(wrapper, type=event_type)
        return callback
    return decorator
