"""Utilidades pequeñas: hora local, JSON y captura de errores de las herramientas."""

from __future__ import annotations

import functools
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_candado = threading.RLock()


def ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def leer_json(ruta: Path, defecto: Any) -> Any:
    with _candado:
        if not ruta.is_file():
            return defecto
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defecto
        return datos


def guardar_json(ruta: Path, datos: Any) -> None:
    with _candado:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(ruta.suffix + ".asistente.tmp")
        temporal.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporal.replace(ruta)


def ocultar_secreto(texto: str) -> str:
    clave = os.environ.get("ASISTENTE_API_KEY", "").strip()
    if len(clave) >= 16 and clave in texto:
        texto = texto.replace(clave, "***")
    return texto[:500]


def capturar(funcion: Callable) -> Callable:
    @functools.wraps(funcion)
    def envoltura(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        except Exception as exc:
            from back.rutas import PermisoRuta

            if isinstance(exc, (PermisoRuta, ValueError, OSError, RuntimeError)):
                return {"ok": False, "texto": str(exc)}
            return {"ok": False, "texto": f"No pude completar la acción: {exc}"}

    return envoltura
