"""Cola de acciones que no se ejecutan hasta que el usuario pulsa Aprobar."""

from __future__ import annotations

import re
import threading
import uuid
from typing import Callable

from back.apoyo import ahora, guardar_json, leer_json
from back.config import DATOS
from back import aviso, memoria
from back.registro import registrar

_candado = threading.Lock()
_ARCHIVO = DATOS / "pendientes.json"
_ejecutores: dict[str, Callable[[dict], str]] = {}
_ID = re.compile(r"[0-9a-f]{32}")


def registrar_ejecutor(tipo: str, funcion: Callable[[dict], str]) -> None:
    _ejecutores[tipo] = funcion


def _leer() -> list[dict]:
    datos = leer_json(_ARCHIVO, [])
    return datos if isinstance(datos, list) else []


def _guardar(items: list[dict]) -> None:
    guardar_json(_ARCHIVO, items)


def listar() -> list[dict]:
    with _candado:
        return _leer()


def vaciar() -> None:
    with _candado:
        _guardar([])


def agregar(tipo: str, titulo: str, resumen: str, vista: str, detalle: dict) -> dict:
    if len(vista) > 20000:
        vista = vista[:20000] + "\n… (vista recortada)"
    accion = {
        "id": uuid.uuid4().hex,
        "tipo": tipo,
        "titulo": titulo,
        "resumen": resumen,
        "vista": vista,
        "detalle": detalle,
        "creado": ahora(),
    }
    with _candado:
        items = _leer()
        if len(items) >= 30:
            raise RuntimeError(
                "Hay demasiadas acciones pendientes. Aprueba o rechaza algunas antes de crear más."
            )
        items.append(accion)
        _guardar(items)
    error = aviso.notificar(titulo, resumen)
    with _candado:
        items = _leer()
        for item in items:
            if item.get("id") == accion["id"]:
                item["notificado"] = error is None
                if error:
                    item["aviso_error"] = error
                accion = item
                break
        _guardar(items)
    return accion


def _extraer(id_accion: str) -> dict | None:
    if not _ID.fullmatch(id_accion or ""):
        return None
    items = _leer()
    encontrada = None
    resto = []
    for item in items:
        if item.get("id") == id_accion and encontrada is None:
            encontrada = item
        else:
            resto.append(item)
    if encontrada is not None:
        _guardar(resto)
    return encontrada


def _restaurar(accion: dict) -> None:
    with _candado:
        items = _leer()
        if not any(item.get("id") == accion.get("id") for item in items):
            items.append(accion)
            _guardar(items)


def aprobar(id_accion: str) -> dict:
    if not _ID.fullmatch(id_accion or ""):
        raise ValueError("Identificador no válido.")
    with _candado:
        accion = _extraer(id_accion)
    if accion is None:
        raise KeyError(id_accion)
    ejecutor = _ejecutores.get(accion.get("tipo", ""))
    if ejecutor is None:
        _restaurar(accion)
        raise RuntimeError("No sé ejecutar ese tipo de acción.")
    try:
        mensaje = ejecutor(accion)
    except Exception:
        _restaurar(accion)
        raise
    registrar("aprobada", mensaje)
    try:
        memoria.anotar_decision(True, str(accion.get("titulo") or ""), str(accion.get("resumen") or ""))
    except Exception:
        pass
    return {"ok": True, "mensaje": mensaje, "id": id_accion}


def etiquetar(ids: list[str], chat_id: str, mensaje_id: str) -> None:
    buscados = {valor for valor in ids if isinstance(valor, str)}
    if not buscados:
        return
    with _candado:
        items = _leer()
        for item in items:
            if item.get("id") in buscados:
                item["chat_id"] = chat_id
                item["mensaje_id"] = mensaje_id
        _guardar(items)


def retirar(ids: list[str]) -> dict:
    buscados = [valor for valor in ids if isinstance(valor, str) and _ID.fullmatch(valor)]
    if not buscados:
        return {"retiradas": [], "ausentes": []}
    with _candado:
        items = _leer()
        presentes = {item.get("id") for item in items}
        retiradas = [valor for valor in buscados if valor in presentes]
        ausentes = [valor for valor in buscados if valor not in presentes]
        if retiradas:
            resto = [item for item in items if item.get("id") not in set(retiradas)]
            _guardar(resto)
            registrar("retroceso", f"Retiré {len(retiradas)} acción pendiente al retroceder el chat.")
        return {"retiradas": retiradas, "ausentes": ausentes}


def retirar_chat(chat_id: str) -> list[str]:
    if not chat_id:
        return []
    with _candado:
        items = _leer()
        retiradas = [item.get("id") for item in items if item.get("chat_id") == chat_id and item.get("id")]
        if retiradas:
            _guardar([item for item in items if item.get("chat_id") != chat_id])
        return retiradas


def rechazar(id_accion: str) -> dict:
    if not _ID.fullmatch(id_accion or ""):
        raise ValueError("Identificador no válido.")
    with _candado:
        accion = _extraer(id_accion)
    if accion is None:
        raise KeyError(id_accion)
    titulo = accion.get("titulo") or "la acción"
    mensaje = f"Rechacé «{titulo}». No se modificó nada."
    registrar("rechazada", mensaje)
    try:
        memoria.anotar_decision(False, str(titulo), str(accion.get("resumen") or ""))
    except Exception:
        pass
    return {"ok": True, "mensaje": mensaje, "id": id_accion}
