"""R1: bloqueo temporal de un usuario tras N contraseñas incorrectas seguidas (cuarentena de la cuenta).

Módulo puro (sin red ni LDAP) para poder probarlo con un reloj falso. Las fallas solo cuentan dentro de una ventana de tiempo; al llegar a
`max_intentos` el usuario queda bloqueado `bloqueo_s` segundos y, pasado ese plazo, vuelve a empezar de cero. Un acceso correcto borra su cuenta.
El nombre se normaliza (minúsculas y sin espacios en los bordes) para que `Profesor01` y `profesor01 ` sean la misma cuenta, existan o no en
el LDAP: así el comportamiento no revela qué usuarios existen.

Uso previsto en `/portal/login`: `if limite.is_locked(u): rechazar`; tras un rechazo, `limite.register_failed_attempt(u)`; tras un acceso, `limite.register_success(u)`.
"""
import time
from typing import Callable, Dict, List


class LimiteIntentos:
    def __init__(self, max_intentos: int = 5, ventana_s: float = 300.0, bloqueo_s: float = 900.0,
                 reloj: Callable[[], float] = time.monotonic):
        if max_intentos < 1 or ventana_s <= 0 or bloqueo_s <= 0:
            raise ValueError("max_intentos >= 1, ventana_s > 0 y bloqueo_s > 0")
        self.max_intentos, self.ventana_s, self.bloqueo_s, self._reloj = max_intentos, ventana_s, bloqueo_s, reloj
        self._fallas: Dict[str, List[float]] = {}
        self._hasta: Dict[str, float] = {}

    @staticmethod
    def _clave(usuario: str) -> str:
        return (usuario or "").strip().lower()

    def is_locked(self, usuario: str) -> bool:
        k, ahora = self._clave(usuario), self._reloj()
        hasta = self._hasta.get(k)
        if hasta is None:
            return False
        if ahora >= hasta:                       # el bloqueo terminó: cuenta nueva
            del self._hasta[k]
            self._fallas.pop(k, None)
            return False
        return True

    def register_failed_attempt(self, usuario: str) -> bool:
        """Anota una falla; devuelve True si con ella (o ya antes) la cuenta queda bloqueada."""
        k, ahora = self._clave(usuario), self._reloj()
        if self.is_locked(k):
            return True                          # un usuario ya bloqueado no alarga su bloqueo
        fallas = [t for t in self._fallas.get(k, []) if ahora - t < self.ventana_s]
        fallas.append(ahora)
        if len(fallas) >= self.max_intentos:
            self._hasta[k] = ahora + self.bloqueo_s
            self._fallas.pop(k, None)
            return True
        self._fallas[k] = fallas
        return False

    def register_success(self, usuario: str) -> None:
        self._fallas.pop(self._clave(usuario), None)

    def segundos_restantes(self, usuario: str) -> float:
        if not self.is_locked(usuario):
            return 0.0
        return max(0.0, self._hasta[self._clave(usuario)] - self._reloj())
