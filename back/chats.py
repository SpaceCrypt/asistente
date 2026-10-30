"""Chats locales en datos/: nuevo, fijar, archivar, eliminar y retroceder."""

from __future__ import annotations

import re
import threading
import uuid

from back.apoyo import ahora, guardar_json, leer_json
from back.config import DATOS

_ARCHIVO = DATOS / "chats.json"
_candado = threading.Lock()
_ID = re.compile(r"[0-9a-f]{32}")
_MAX_MENSAJES = 200


def _titulo(texto: str) -> str:
    limpio = " ".join((texto or "").split())
    if not limpio:
        return "Nuevo chat"
    if len(limpio) > 42:
        return limpio[:42].rstrip() + "…"
    return limpio


def _uso_vacio() -> dict:
    return {"entrada": None, "salida": None, "segundos": None}


def _nuevo() -> dict:
    momento = ahora()
    return {
        "id": uuid.uuid4().hex,
        "titulo": "Nuevo chat",
        "mensajes": [],
        "fijado": False,
        "archivado": False,
        "creado": momento,
        "actualizado": momento,
        "hueco": None,
        "uso": _uso_vacio(),
    }


def _guardar(datos: dict) -> None:
    guardar_json(_ARCHIVO, datos)


def _cargar() -> dict:
    datos = leer_json(_ARCHIVO, None)
    chats = datos.get("chats") if isinstance(datos, dict) else None
    if not isinstance(chats, list) or not chats:
        chat = _nuevo()
        datos = {"activo": chat["id"], "chats": [chat]}
        _guardar(datos)
        return datos
    if not any(isinstance(chat, dict) and chat.get("id") == datos.get("activo") for chat in chats):
        datos["activo"] = chats[0].get("id")
        _guardar(datos)
    return datos


def _exigir_id(valor: str) -> str:
    if not _ID.fullmatch(valor or ""):
        raise ValueError("Identificador de chat no válido.")
    return valor


def _buscar(datos: dict, chat_id: str | None) -> dict:
    buscado = chat_id or datos.get("activo")
    for chat in datos["chats"]:
        if isinstance(chat, dict) and chat.get("id") == buscado:
            return chat
    raise KeyError(buscado or "")


def _ordenar(chats: list[dict]) -> list[dict]:
    por_fecha = sorted(chats, key=lambda chat: chat.get("actualizado") or "", reverse=True)
    fijados = [chat for chat in por_fecha if chat.get("fijado")]
    resto = [chat for chat in por_fecha if not chat.get("fijado")]
    return fijados + resto


def _recalcular_uso(chat: dict) -> None:
    entrada = None
    salida = None
    segundos = None
    for mensaje in chat.get("mensajes") or []:
        uso = mensaje.get("uso") if isinstance(mensaje.get("uso"), dict) else None
        if not uso:
            continue
        if isinstance(uso.get("prompt_eval_count"), int):
            entrada = (entrada or 0) + uso["prompt_eval_count"]
        if isinstance(uso.get("eval_count"), int):
            salida = (salida or 0) + uso["eval_count"]
        if isinstance(uso.get("segundos"), (int, float)) and not isinstance(uso.get("segundos"), bool):
            segundos = round((segundos or 0) + float(uso["segundos"]), 2)
    chat["uso"] = {"entrada": entrada, "salida": salida, "segundos": segundos}


def _ultimo_uso(chat: dict) -> dict:
    for mensaje in reversed(chat.get("mensajes") or []):
        uso = mensaje.get("uso") if isinstance(mensaje.get("uso"), dict) else None
        if mensaje.get("role") == "assistant" and uso:
            return {
                "prompt_eval_count": uso.get("prompt_eval_count"),
                "eval_count": uso.get("eval_count"),
                "tokens_por_segundo": uso.get("tokens_por_segundo"),
                "segundos": uso.get("segundos"),
                "detenido": bool(mensaje.get("detenido")),
            }
    return {
        "prompt_eval_count": None,
        "eval_count": None,
        "tokens_por_segundo": None,
        "segundos": None,
        "detenido": False,
    }


def _actualizar_titulo(chat: dict) -> None:
    for mensaje in chat.get("mensajes") or []:
        if mensaje.get("role") == "user":
            chat["titulo"] = _titulo(mensaje.get("content") or "")
            return
    chat["titulo"] = "Nuevo chat"


def _ficha(chat: dict) -> dict:
    trozos = []
    for mensaje in chat.get("mensajes") or []:
        if not isinstance(mensaje, dict) or mensaje.get("interno"):
            continue
        texto = str(mensaje.get("content") or "").strip()
        if texto:
            trozos.append(texto)
    return {
        "id": chat.get("id"),
        "titulo": chat.get("titulo") or "Nuevo chat",
        "fijado": bool(chat.get("fijado")),
        "archivado": bool(chat.get("archivado")),
        "creado": chat.get("creado"),
        "actualizado": chat.get("actualizado"),
        "uso": chat.get("uso") or _uso_vacio(),
        "texto": "\n".join(trozos)[:4000],
    }


def _publico(mensaje: dict) -> dict:
    return {
        "id": mensaje.get("id"),
        "role": mensaje.get("role"),
        "content": mensaje.get("content") or "",
        "creado": mensaje.get("creado"),
        "detenido": bool(mensaje.get("detenido")),
    }


def _empaquetar(datos: dict) -> dict:
    activo = _buscar(datos, datos.get("activo"))
    return {
        "ok": True,
        "activo": activo.get("id"),
        "chats": [_ficha(chat) for chat in _ordenar(datos["chats"])],
        "mensajes": [_publico(mensaje) for mensaje in activo.get("mensajes") or []],
        "uso": activo.get("uso") or _uso_vacio(),
        "ultimo": _ultimo_uso(activo),
    }


def resumen() -> dict:
    with _candado:
        return _empaquetar(_cargar())


def activo_id() -> str:
    with _candado:
        return str(_cargar().get("activo") or "")


def uso_activo(chat_id: str | None = None) -> dict:
    with _candado:
        chat = _buscar(_cargar(), chat_id)
        return dict(chat.get("uso") or _uso_vacio())


def mensajes_modelo(chat_id: str | None = None, limite: int = 16) -> list[dict]:
    with _candado:
        chat = _buscar(_cargar(), chat_id)
        recientes = (chat.get("mensajes") or [])[-limite:]
        salida = []
        for mensaje in recientes:
            if mensaje.get("role") not in {"user", "assistant"}:
                continue
            contenido = mensaje.get("content") or ""
            interno = mensaje.get("interno") or ""
            if interno:
                contenido = f"{contenido}\n{interno}" if contenido else interno
            salida.append({"role": mensaje.get("role"), "content": contenido})
        return salida


def leer_hueco(chat_id: str | None = None) -> dict | None:
    with _candado:
        chat = _buscar(_cargar(), chat_id)
        hueco = chat.get("hueco")
        return dict(hueco) if isinstance(hueco, dict) else None


def poner_hueco(datos: dict | None, chat_id: str | None = None) -> None:
    with _candado:
        estado = _cargar()
        chat = _buscar(estado, chat_id)
        chat["hueco"] = datos
        _guardar(estado)


def nuevo() -> dict:
    with _candado:
        estado = _cargar()
        chat = _nuevo()
        estado["chats"].append(chat)
        estado["activo"] = chat["id"]
        _guardar(estado)
        return _empaquetar(estado)


def activar(chat_id: str) -> dict:
    _exigir_id(chat_id)
    with _candado:
        estado = _cargar()
        _buscar(estado, chat_id)
        estado["activo"] = chat_id
        _guardar(estado)
        return _empaquetar(estado)


def fijar(chat_id: str, valor: bool) -> dict:
    _exigir_id(chat_id)
    with _candado:
        estado = _cargar()
        chat = _buscar(estado, chat_id)
        chat["fijado"] = bool(valor)
        chat["actualizado"] = ahora()
        _guardar(estado)
        return _empaquetar(estado)


def archivar(chat_id: str, valor: bool) -> dict:
    _exigir_id(chat_id)
    with _candado:
        estado = _cargar()
        chat = _buscar(estado, chat_id)
        chat["archivado"] = bool(valor)
        chat["actualizado"] = ahora()
        if valor and estado.get("activo") == chat_id:
            otros = [
                candidato
                for candidato in _ordenar(estado["chats"])
                if not candidato.get("archivado") and candidato.get("id") != chat_id
            ]
            if otros:
                estado["activo"] = otros[0]["id"]
        _guardar(estado)
        return _empaquetar(estado)


def _recolectar(mensajes: list[dict]) -> tuple[list[str], list[str], list[str]]:
    pendientes: list[str] = []
    memoria: list[str] = []
    turnos: list[str] = []
    for mensaje in mensajes:
        if isinstance(mensaje.get("id"), str):
            turnos.append(mensaje["id"])
        for clave, destino in (("pendientes", pendientes), ("memoria_ids", memoria)):
            for valor in mensaje.get(clave) or []:
                if isinstance(valor, str) and valor not in destino:
                    destino.append(valor)
    return pendientes, memoria, turnos


def eliminar(chat_id: str) -> dict:
    _exigir_id(chat_id)
    with _candado:
        estado = _cargar()
        chat = _buscar(estado, chat_id)
        pendientes, memoria, turnos = _recolectar(chat.get("mensajes") or [])
        estado["chats"] = [candidato for candidato in estado["chats"] if candidato.get("id") != chat_id]
        if not estado["chats"]:
            nuevo_chat = _nuevo()
            estado["chats"] = [nuevo_chat]
            estado["activo"] = nuevo_chat["id"]
        elif estado.get("activo") == chat_id:
            vivos = [candidato for candidato in _ordenar(estado["chats"]) if not candidato.get("archivado")]
            estado["activo"] = (vivos or estado["chats"])[0]["id"]
        _guardar(estado)
        empaquetado = _empaquetar(estado)
    empaquetado["pendientes"] = pendientes
    empaquetado["memoria_ids"] = memoria
    empaquetado["turnos"] = turnos
    empaquetado["mensaje"] = "Eliminé el chat. Los archivos que ya aprobaste siguen en el disco."
    return empaquetado


def retroceder(chat_id: str | None, mensaje_id: str) -> dict:
    if not _ID.fullmatch(mensaje_id or ""):
        raise ValueError("Identificador de mensaje no válido.")
    if chat_id:
        _exigir_id(chat_id)
    with _candado:
        estado = _cargar()
        if chat_id:
            estado["activo"] = chat_id
        chat = _buscar(estado, estado.get("activo"))
        mensajes = chat.get("mensajes") or []
        indice = next((i for i, mensaje in enumerate(mensajes) if mensaje.get("id") == mensaje_id), None)
        if indice is None:
            raise KeyError(mensaje_id)
        if mensajes[indice].get("role") != "user":
            raise ValueError("Solo se puede editar un mensaje tuyo.")
        cortados = mensajes[indice:]
        pendientes, memoria, turnos = _recolectar(cortados)
        chat["mensajes"] = mensajes[:indice]
        chat["hueco"] = None
        _recalcular_uso(chat)
        _actualizar_titulo(chat)
        chat["actualizado"] = ahora()
        _guardar(estado)
        return {
            "chat_id": chat["id"],
            "pendientes": pendientes,
            "memoria_ids": memoria,
            "turnos": turnos,
        }


def anexar_turno(
    usuario: str,
    asistente: str,
    pendientes: list[str],
    uso: dict,
    memoria_id: str,
    interno: str = "",
    detenido: bool = False,
    chat_id: str | None = None,
) -> dict:
    momento = ahora()
    id_usuario = uuid.uuid4().hex
    id_asistente = uuid.uuid4().hex
    with _candado:
        estado = _cargar()
        chat = _buscar(estado, chat_id)
        chat.setdefault("mensajes", []).append(
            {
                "id": id_usuario,
                "role": "user",
                "content": usuario,
                "creado": momento,
                "pendientes": [],
                "memoria_ids": [memoria_id] if memoria_id else [],
            }
        )
        mensaje_asistente = {
            "id": id_asistente,
            "role": "assistant",
            "content": asistente,
            "creado": momento,
            "pendientes": [valor for valor in pendientes if isinstance(valor, str)],
            "memoria_ids": [],
            "uso": uso or {},
            "detenido": detenido,
        }
        if interno:
            mensaje_asistente["interno"] = interno
        chat["mensajes"].append(mensaje_asistente)
        if len(chat["mensajes"]) > _MAX_MENSAJES:
            chat["mensajes"] = chat["mensajes"][-_MAX_MENSAJES:]
        _actualizar_titulo(chat)
        _recalcular_uso(chat)
        chat["actualizado"] = momento
        _guardar(estado)
        return {
            "chat_id": chat["id"],
            "mensaje_usuario": id_usuario,
            "mensaje_asistente": id_asistente,
            "uso": chat.get("uso") or _uso_vacio(),
        }
