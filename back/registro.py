"""Registro local de lecturas y de acciones ya decididas."""

from __future__ import annotations

import json
import threading

from back.apoyo import ahora
from back.config import DATOS, asegurar_datos

_candado = threading.Lock()
_ARCHIVO = DATOS / "registro.jsonl"
_TOPE = 800


def registrar(categoria: str, mensaje: str) -> None:
    texto = " ".join(mensaje.split())
    if len(texto) > 300:
        texto = texto[:300] + "…"
    linea = json.dumps(
        {"ts": ahora(), "categoria": categoria, "mensaje": texto},
        ensure_ascii=False,
    )
    with _candado:
        asegurar_datos()
        with _ARCHIVO.open("a", encoding="utf-8") as archivo:
            archivo.write(linea + "\n")
        _recortar()


def _recortar() -> None:
    if not _ARCHIVO.is_file():
        return
    lineas = _ARCHIVO.read_text(encoding="utf-8").splitlines()
    if len(lineas) <= _TOPE:
        return
    _ARCHIVO.write_text("\n".join(lineas[-500:]) + "\n", encoding="utf-8")


def vaciar() -> None:
    with _candado:
        asegurar_datos()
        _ARCHIVO.write_text("", encoding="utf-8")


def recientes(limite: int = 20) -> list[dict]:
    if not _ARCHIVO.is_file():
        return []
    try:
        lineas = _ARCHIVO.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    salida = []
    for linea in lineas[-limite:]:
        try:
            salida.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return salida
