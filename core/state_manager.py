"""
state_manager.py — State Manager único para Aether.

Responsabilidades:
  - Gestionar todo el estado del sistema en un único lugar
  - Garantizar consistencia entre RAM, DB y conexiones externas
  - Soportar snapshots y restauraciones
  - Facilitar debugging y tests

Arquitectura:
  1. StateManager: clase principal (singleton recomendado)
  2. StateSnapshot: representación inmutable del estado
  3. StateDiff: representación de cambios entre snapshots

Uso:
    from core.state_manager import StateManager
    
    state = StateManager()
    
    # Leer
    orden = state.get("orden")
    
    # Escribir
    state.set("orden", "nueva orden")
    
    # Snapshot
    snapshot = state.snapshot()
    
    # Restaurar
    state.restore(snapshot)
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class StateSnapshot:
    """
    Snapshot inmutable del estado.
    
    Args:
        id: Identificador único del snapshot
        timestamp: Momento de creación
        ram_state: Estado en RAM
        db_state: Estado persistido (referencia)
        version: Versión del estado
    """
    id: str
    timestamp: float
    ram_state: Dict[str, Any]
    db_state: Dict[str, Any]
    version: int


@dataclass
class StateDiff:
    """
    Diferencia entre dos snapshots.
    
    Args:
        snapshot_before: Snapshot anterior
        snapshot_after: Snapshot posterior
        changes: Dict con claves cambiadas y sus valores
    """
    snapshot_before: StateSnapshot
    snapshot_after: StateSnapshot
    changes: Dict[str, Tuple[Any, Any]] = field(default_factory=dict)


class StateManager:
    """
    Gestor de estado centralizado para Aether.
    
    Características:
      - Estado único (RAM + DB + MCP)
      - Consistencia garantizada
      - Snapshots fáciles
      - Debugging sencillo
    """
    
    def __init__(
        self,
        ram_state: Optional[Dict[str, Any]] = None,
        db_state: Optional[Dict[str, Any]] = None
    ):
        self._ram_state: Dict[str, Any] = ram_state or {}
        self._db_state: Dict[str, Any] = db_state or {}
        self._version: int = 0
        self._lock = threading.RLock()
        self._snapshot_history: List[StateSnapshot] = []
        self._max_snapshots = 10
    
    # --- Propiedades ---
    
    @property
    def ram_state(self) -> Dict[str, Any]:
        """Estado en RAM."""
        with self._lock:
            return dict(self._ram_state)
    
    @property
    def db_state(self) -> Dict[str, Any]:
        """Estado en DB (referencia)."""
        with self._lock:
            return dict(self._db_state)
    
    @property
    def version(self) -> int:
        """Versión actual del estado."""
        with self._lock:
            return self._version
    
    # --- API pública ---
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Obtener valor del estado.
        
        Args:
            key: Clave del valor
            default: Valor por defecto si no existe
        
        Returns:
            Valor del estado o default
        """
        with self._lock:
            return self._ram_state.get(key, default)
    
    def get_all(self) -> Dict[str, Any]:
        """Obtener todo el estado."""
        with self._lock:
            return dict(self._ram_state)
    
    def set(self, key: str, value: Any) -> None:
        """
        Establecer valor en el estado.
        
        Args:
            key: Clave del valor
            value: Nuevo valor
        """
        with self._lock:
            old_value = self._ram_state.get(key)
            self._ram_state[key] = value
            self._version += 1
            
            # TODO: Persistir en DB si es necesario
            # self._persist_to_db(key, value)
    
    def set_multiple(self, state: Dict[str, Any]) -> None:
        """
        Establecer múltiples valores.
        
        Args:
            state: Dict con claves y valores
        """
        with self._lock:
            for key, value in state.items():
                self._ram_state[key] = value
            self._version += 1
    
    def delete(self, key: str) -> bool:
        """
        Eliminar clave del estado.
        
        Args:
            key: Clave a eliminar
        
        Returns:
            True si existía y se eliminó
        """
        with self._lock:
            if key in self._ram_state:
                del self._ram_state[key]
                self._version += 1
                return True
            return False
    
    # --- Snapshots ---
    
    def snapshot(self, description: str = "") -> StateSnapshot:
        """
        Crear snapshot del estado actual.
        
        Args:
            description: Descripción opcional
        
        Returns:
            StateSnapshot
        """
        with self._lock:
            snapshot = StateSnapshot(
                id=str(uuid.uuid4())[:8],
                timestamp=datetime.now().timestamp(),
                ram_state=dict(self._ram_state),
                db_state=dict(self._db_state),
                version=self._version
            )
            
            # Guardar en historial
            self._snapshot_history.append(snapshot)
            if len(self._snapshot_history) > self._max_snapshots:
                self._snapshot_history.pop(0)
            
            return snapshot
    
    def restore(self, snapshot: StateSnapshot) -> None:
        """
        Restaurar estado desde snapshot.
        
        Args:
            snapshot: Snapshot a restaurar
        """
        with self._lock:
            self._ram_state = dict(snapshot.ram_state)
            # self._db_state = dict(snapshot.db_state)
            self._version = snapshot.version
    
    def restore_to_version(self, version: int) -> bool:
        """
        Restaurar a una versión específica.
        
        Args:
            version: Número de versión
        
        Returns:
            True si se encontró y restauró
        """
        with self._lock:
            for snapshot in reversed(self._snapshot_history):
                if snapshot.version <= version:
                    self.restore(snapshot)
                    return True
            return False
    
    def get_snapshot(self, snapshot_id: str) -> Optional[StateSnapshot]:
        """
        Obtener snapshot por ID.
        
        Args:
            snapshot_id: ID del snapshot
        
        Returns:
            Snapshot o None
        """
        with self._lock:
            for snapshot in self._snapshot_history:
                if snapshot.id == snapshot_id:
                    return snapshot
            return None
    
    def diff(self, before: StateSnapshot, after: StateSnapshot) -> StateDiff:
        """
        Calcular diferencia entre dos snapshots.
        
        Args:
            before: Snapshot anterior
            after: Snapshot posterior
        
        Returns:
            StateDiff con los cambios
        """
        changes = {}
        
        with self._lock:
            # Comparar RAM
            all_keys = set(before.ram_state.keys()) | set(after.ram_state.keys())
            for key in all_keys:
                old_val = before.ram_state.get(key)
                new_val = after.ram_state.get(key)
                if old_val != new_val:
                    changes[key] = (old_val, new_val)
        
        return StateDiff(
            snapshot_before=before,
            snapshot_after=after,
            changes=changes
        )
    
    # --- Utilidades ---
    
    def clear(self) -> None:
        """Limpiar todo el estado."""
        with self._lock:
            self._ram_state.clear()
            self._db_state.clear()
            self._version += 1
    
    def reset(self) -> None:
        """Resetear estado a valores por defecto."""
        with self._lock:
            self._ram_state = {}
            self._db_state = {}
            self._version = 0
            self._snapshot_history.clear()
    
    def get_history(self, limit: int = 10) -> List[StateSnapshot]:
        """
        Obtener historial de snapshots.
        
        Args:
            limit: Máximo de snapshots
        
        Returns:
            Lista de snapshots (más recientes primero)
        """
        with self._lock:
            return self._snapshot_history[-limit:]
    
    def __repr__(self) -> str:
        with self._lock:
            return f"<StateManager version={self._version} keys={len(self._ram_state)}>"
