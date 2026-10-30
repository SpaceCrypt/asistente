"""Abre el asistente en una ventana propia. No carga ningún modelo ni abre el navegador."""

from __future__ import annotations

import os
import sys


def _asegurar_salida() -> None:
    """Un exe sin consola deja stdout y stderr en None. Uvicorn no sobrevive a eso."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


_asegurar_salida()

import threading
import time
import urllib.error
import urllib.request

import uvicorn
import webview

from back.main import app

_ORIGEN = "http://127.0.0.1:8765"


def _esperar() -> None:
    for _ in range(40):
        if _en_marcha():
            return
        time.sleep(0.25)


def _en_marcha() -> bool:
    try:
        with urllib.request.urlopen(_ORIGEN + "/salud", timeout=1) as respuesta:
            return respuesta.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> None:
    _asegurar_salida()
    if not _en_marcha():
        config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="info")
        servidor = uvicorn.Server(config)
        hilo = threading.Thread(target=servidor.run, daemon=True)
        hilo.start()
        _esperar()
    webview.create_window(
        "Asistente personal",
        _ORIGEN,
        width=1180,
        height=800,
        min_size=(800, 560),
    )
    webview.start()


if __name__ == "__main__":
    main()
