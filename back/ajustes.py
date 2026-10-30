"""Modelo elegido y modo de recursos. No carga nada en la gráfica al guardar."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from back.apoyo import guardar_json, leer_json
from back.config import DATOS

_ARCHIVO = DATOS / "ajustes.json"
_MODOS = ("ram", "mixto", "gpu")
_VRAM_TOPE = 5_000_000_000
_GRANDES = re.compile(r"(?:^|[^0-9])(?:7|8|14|32)\s*b\b", re.IGNORECASE)


def _base_ollama() -> str:
    base = os.environ.get("ASISTENTE_BASE_URL", "http://127.0.0.1:11434/v1").strip()
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base or "http://127.0.0.1:11434"


def _gb(tamano: int) -> str:
    if tamano <= 0:
        return "—"
    texto = f"{tamano / 1_000_000_000:.1f}"
    return texto.replace(".", ",") + " GB"


def _leer() -> dict:
    datos = leer_json(_ARCHIVO, {})
    if not isinstance(datos, dict):
        datos = {}
    modelo = str(datos.get("modelo") or "").strip()
    if not modelo:
        modelo = os.environ.get("ASISTENTE_MODELO", "qwen3:8b").strip() or "qwen3:8b"
    modo = str(datos.get("modo") or os.environ.get("ASISTENTE_MODO", "ram")).strip().lower()
    if modo not in _MODOS:
        modo = "ram"
    return {"modelo": modelo, "modo": modo}


def guardar(modelo: str | None = None, modo: str | None = None) -> dict:
    actual = _leer()
    if modelo is not None:
        actual["modelo"] = modelo.strip()
    if modo is not None:
        actual["modo"] = modo.strip().lower()
    if actual["modo"] not in _MODOS:
        raise ValueError("Ese modo de recursos no existe.")
    guardar_json(_ARCHIVO, actual)
    return actual


def modelo() -> str:
    return _leer()["modelo"]


def modo() -> str:
    return _leer()["modo"]


def cabe_en_grafica(nombre: str, tamano: int = 0) -> bool:
    if _GRANDES.search(nombre or ""):
        return False
    if tamano >= _VRAM_TOPE:
        return False
    return True


def capas_del_modo(modo_pedido: str, nombre: str = "", tamano: int = 0) -> int:
    """0 en Solo RAM, 12 en el reparto y todas las capas en Solo gráfica."""
    if modo_pedido == "mixto":
        return 12
    if modo_pedido == "gpu":
        return 99
    return 0


def _instalados() -> list[dict]:
    solicitud = urllib.request.Request(_base_ollama() + "/api/tags", method="GET")
    with urllib.request.urlopen(solicitud, timeout=4) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))
    crudos = datos.get("models") if isinstance(datos, dict) else None
    if not isinstance(crudos, list):
        return []
    salida = []
    vistos = set()
    for item in crudos:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("name") or item.get("model") or "").strip()
        if not nombre or nombre in vistos:
            continue
        vistos.add(nombre)
        try:
            tamano = int(item.get("size") or 0)
        except (TypeError, ValueError):
            tamano = 0
        detalle = item.get("details") if isinstance(item.get("details"), dict) else {}
        salida.append(
            {
                "nombre": nombre,
                "tamano": tamano,
                "parametros": str(detalle.get("parameter_size") or ""),
                "cuantizacion": str(detalle.get("quantization_level") or ""),
            }
        )
    salida.sort(key=lambda modelo_item: (modelo_item["tamano"], modelo_item["nombre"]))
    return salida


def _etiqueta(indice: int, total: int) -> str:
    if total <= 1:
        return "Único instalado"
    if indice == 0:
        return "El que menos consume"
    if indice == total - 1:
        return "El que más consume"
    return "Intermedio"


def _frase_modelo(tamano: int, cabe: bool) -> str:
    disco = _gb(tamano)
    memoria = disco if tamano else "—"
    extra = " Puede llenar la gráfica." if not cabe else ""
    return f"Ocupa {disco} en disco y, al cargarse, unos {memoria} de pesos.{extra}"


def _frase_modo(modo_pedido: str, cabe: bool) -> str:
    if modo_pedido == "ram":
        return "Esto no toca la gráfica. El modelo trabaja en la memoria del equipo."
    if modo_pedido == "mixto":
        return "Usa un poco de gráfica (12 capas, nunca más de 16) y el resto en memoria."
    aviso = " Puede llenar la gráfica." if not cabe else ""
    return "Manda todas las capas a la gráfica." + aviso


def soltar_si_cargado(nombre: str) -> None:
    """Pide a Ollama que suelte ese modelo si ya está en memoria. No manda un prompt."""
    if not nombre:
        return
    try:
        solicitud = urllib.request.Request(_base_ollama() + "/api/ps", method="GET")
        with urllib.request.urlopen(solicitud, timeout=3) as respuesta:
            datos = json.loads(respuesta.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return
    cargados = datos.get("models") if isinstance(datos, dict) else None
    if not isinstance(cargados, list):
        return
    activo = False
    for item in cargados:
        if not isinstance(item, dict):
            continue
        candidato = str(item.get("name") or item.get("model") or "")
        if candidato == nombre or nombre in candidato or candidato in nombre:
            activo = True
            break
    if not activo:
        return
    cuerpo = json.dumps({"model": nombre, "keep_alive": 0}).encode("utf-8")
    pedido = urllib.request.Request(
        _base_ollama() + "/api/generate",
        data=cuerpo,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=8) as respuesta:
            respuesta.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def catalogo() -> dict:
    try:
        instalados = _instalados()
        aviso = ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        instalados = []
        aviso = "No pude leer la lista de Ollama. No invento modelos."
    guardado = _leer()
    nombres = {item["nombre"] for item in instalados}
    elegido = guardado["modelo"] if guardado["modelo"] in nombres or not instalados else instalados[0]["nombre"]
    tamano_elegido = 0
    for item in instalados:
        if item["nombre"] == elegido:
            tamano_elegido = item["tamano"]
            break
    modo_actual = guardado["modo"]
    modelos = []
    total = len(instalados)
    for indice, item in enumerate(instalados):
        cabe = cabe_en_grafica(item["nombre"], item["tamano"])
        modelos.append(
            {
                "nombre": item["nombre"],
                "disco": _gb(item["tamano"]),
                "memoria": _gb(item["tamano"]),
                "parametros": item["parametros"],
                "cuantizacion": item["cuantizacion"],
                "etiqueta": _etiqueta(indice, total),
                "frase": _frase_modelo(item["tamano"], cabe),
                "cabe_en_grafica": cabe,
                "elegido": item["nombre"] == elegido,
            }
        )
    cabe_elegido = cabe_en_grafica(elegido, tamano_elegido)
    modos = []
    for identificador, titulo in (
        ("ram", "Solo RAM"),
        ("mixto", "Gráfica y RAM"),
        ("gpu", "Solo gráfica"),
    ):
        modos.append(
            {
                "id": identificador,
                "titulo": titulo,
                "frase": _frase_modo(identificador, cabe_elegido),
                "disponible": True,
                "activo": modo_actual == identificador,
                "capas": capas_del_modo(identificador, elegido, tamano_elegido),
            }
        )
    return {
        "ok": True,
        "modelo": elegido,
        "modo": modo_actual,
        "contexto": 2048,
        "capas": capas_del_modo(modo_actual, elegido, tamano_elegido),
        "modelos": modelos,
        "modos": modos,
        "nota": aviso or "No hay más modelos instalados.",
        "frase": _frase_modo(modo_actual, cabe_elegido),
        "aviso": "" if cabe_elegido else "Puede llenar la gráfica.",
    }


def aplicar(modelo_nuevo: str | None, modo_nuevo: str | None) -> dict:
    actual = _leer()
    instalados = []
    try:
        instalados = _instalados()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        instalados = []
    nombres = {item["nombre"] for item in instalados}
    destino = actual["modelo"]
    if modelo_nuevo is not None and modelo_nuevo.strip():
        destino = modelo_nuevo.strip()
        if nombres and destino not in nombres:
            raise ValueError("Ese modelo no está instalado. No descargo modelos nuevos.")
    modo_destino = actual["modo"] if modo_nuevo is None or not str(modo_nuevo).strip() else str(modo_nuevo).strip().lower()
    if modo_destino not in _MODOS:
        raise ValueError("Ese modo de recursos no existe.")
    if destino != actual["modelo"]:
        soltar_si_cargado(actual["modelo"])
    guardar(destino, modo_destino)
    return catalogo()
