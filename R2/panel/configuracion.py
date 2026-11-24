"""configuracion.py: la foto de la configuración estática que deja `exportar_config.py` (JSON), para el panel. Se vuelve a leer sola cuando el archivo cambia (la regenera `panel_config.yml`), así que
no hace falta reiniciar el panel. Si el archivo falta o está roto, conserva la última foto buena y dice el motivo."""
import json
import os
import time
from typing import Callable, Optional


class ConfigEstatica:
    def __init__(self, ruta: Optional[str], reloj: Callable[[], float] = time.time):
        self.ruta, self.reloj = ruta, reloj
        self._datos: Optional[dict] = None
        self._firma = None
        self._error: Optional[str] = None

    def _cargar(self) -> None:
        if not self.ruta:
            self._error = "no se configuró --config-estatica"
            return
        try:
            st = os.stat(self.ruta)
        except OSError:
            self._error = f"todavía no hay {self.ruta} (lo genera panel_config.yml)"
            return
        firma = (st.st_mtime_ns, st.st_size)
        if firma == self._firma:
            return
        try:
            with open(self.ruta, encoding="utf-8") as f:
                datos = json.load(f)
            if not isinstance(datos, dict) or "archivos" not in datos:
                raise ValueError("no tiene la forma esperada")
            self._datos, self._firma, self._error = datos, firma, None
        except (OSError, ValueError) as e:
            self._error = f"no se pudo leer {self.ruta}: {e}"

    def estado(self) -> dict:
        """{disponible, generado, edad_s, error}"""
        self._cargar()
        g = (self._datos or {}).get("generado")
        return {"disponible": self._datos is not None, "generado": g, "edad_s": round(self.reloj() - g) if isinstance(g, (int, float)) else None, "error": self._error}

    def archivos(self) -> list:
        """Lista para la pantalla: sin los contenidos (pesan) ni los diffs completos."""
        self._cargar()
        return [{k: v for k, v in a.items() if k not in ("contenidos", "diferencias")} | {"n_diferencias": len(a.get("diferencias", []))} for a in (self._datos or {}).get("archivos", [])]

    def archivo(self, ident: str) -> Optional[dict]:
        self._cargar()
        return next((a for a in (self._datos or {}).get("archivos", []) if a["id"] == ident), None)

    def politica(self, con_esperados: bool = False) -> Optional[dict]:
        self._cargar()
        p = (self._datos or {}).get("politica")
        if p is None:
            return None
        return p if con_esperados else {k: v for k, v in p.items() if k != "esperados"}
