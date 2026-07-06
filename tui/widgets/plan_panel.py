"""
Widget Plan Panel para mostrar el progreso de ejecución del plan en la TUI.
Muestra pasos completados y actualiza visualmente según eventos del motor.
"""


from textual.widgets import Static, Label


class PlanPanel(Static):
    """Panel que muestra el estado del plan de ejecucion."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._plan_pasos = []
        self._plan_index = 0
        self._plan_activo = False

    def actualizar(self, plan_pasos=None, plan_index=None, plan_activo=None):
        """Actualiza los datos del panel de plan."""
        if plan_pasos is not None:
            self._plan_pasos = plan_pasos
        if plan_index is not None:
            self._plan_index = plan_index
        if plan_activo is not None:
            self._plan_activo = plan_activo

    def _repintar(self):
        """Reconstruye el contenido visual del panel."""
        texto = ""
        for i, paso in enumerate(self._plan_pasos):
            estado = "✓" if (i + 1) <= self._plan_index else "○"
            texto += f"{estado} {paso}\n"

        activo_texto = "Ejecutando..." if self._plan_activo else ""
        texto += f"\n{activo_texto}"
        self.update(texto)

    def reset(self):
        """Resetea el panel a su estado inicial."""
        self._plan_pasos = []
        self._plan_index = 0
        self._plan_activo = False
        self._repintar()

    @property
    def plan_pasos(self) -> list:
        return self._plan_pasos

    @property
    def plan_index(self) -> int:
        return self._plan_index

    @property
    def plan_activo(self) -> bool:
        return self._plan_activo