"""Rutas del proyecto y carga manual de .env, sin dependencias extra."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _proyecto() -> Path:
    if getattr(sys, "frozen", False):
        aqui = Path(sys.executable).resolve().parent
        for candidato in (aqui, aqui.parent, aqui.parent.parent):
            if (candidato / ".env").is_file() or (candidato / "datos").is_dir():
                return candidato
        return aqui
    return Path(__file__).resolve().parent.parent


def _front() -> Path:
    if getattr(sys, "frozen", False):
        embebido = Path(getattr(sys, "_MEIPASS", "")) / "front"
        if embebido.is_dir():
            return embebido
        return _proyecto() / "front"
    return Path(__file__).resolve().parent.parent / "front"


PROYECTO = _proyecto()
DATOS = PROYECTO / "datos"
WEB = _front()


def cargar_env(ruta: Path | None = None) -> None:
    archivo = ruta or (PROYECTO / ".env")
    if not archivo.is_file():
        return
    try:
        contenido = archivo.read_text(encoding="utf-8")
    except OSError:
        return
    for linea in contenido.splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave, valor = limpia.split("=", 1)
        clave = clave.strip()
        if not clave or clave in os.environ:
            continue
        os.environ[clave] = valor.strip().strip('"').strip("'")


def asegurar_datos() -> Path:
    DATOS.mkdir(parents=True, exist_ok=True)
    return DATOS


cargar_env()
asegurar_datos()
