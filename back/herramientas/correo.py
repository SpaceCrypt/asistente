"""Correo: el borrador queda pendiente. Enviar es otra aprobación distinta."""

from __future__ import annotations

import base64
import re
import uuid
from email.message import EmailMessage

from back import aprobacion
from back.apoyo import ahora, guardar_json, leer_json, capturar
from back.config import DATOS
from back.googleacceso import estado, mensaje_error_google, servicio

_ARCHIVO = DATOS / "borradores.json"
_CORREO = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


def _todos() -> list[dict]:
    datos = leer_json(_ARCHIVO, [])
    return datos if isinstance(datos, list) else []


def contar() -> int:
    return len(_todos())


def _guardar(items: list[dict]) -> None:
    guardar_json(_ARCHIVO, items)


def _gmail_listo() -> bool:
    return bool(estado().get("conectado"))


def _mensaje_raw(para: str, asunto: str, cuerpo: str) -> str:
    mensaje = EmailMessage()
    mensaje["To"] = para
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo, subtype="plain", charset="utf-8")
    return base64.urlsafe_b64encode(mensaje.as_bytes()).decode()


@capturar
def redactar(para: str, asunto: str, cuerpo: str) -> dict:
    destino = (para or "").strip()
    titulo = (asunto or "").strip()
    texto = cuerpo if isinstance(cuerpo, str) else ""
    texto = texto.strip()
    if not _CORREO.fullmatch(destino):
        raise ValueError("El destinatario no parece un correo.")
    if not titulo:
        raise ValueError("El correo necesita un asunto.")
    if not texto:
        raise ValueError("El correo necesita un cuerpo.")
    if len(titulo) > 200 or len(texto) > 20000:
        raise ValueError("El asunto o el cuerpo son demasiado largos.")
    conectado = _gmail_listo()
    if conectado:
        nota = "Al aprobar se crea un borrador en Gmail. No se envía."
        donde = "en Gmail, como borrador"
    else:
        nota = "Gmail no está conectado. Al aprobar se guarda solo en este equipo (datos/borradores.json). No se envía."
        donde = "solo en este equipo"
    vista = f"Para: {destino}\nAsunto: {titulo}\n\n{texto}\n\n{nota}"
    pendiente = aprobacion.agregar(
        "borrador_correo",
        "Guardar borrador de correo",
        f"{destino} — {titulo}",
        vista,
        {"para": destino, "asunto": titulo, "cuerpo": texto, "nota": nota},
    )
    return {
        "ok": True,
        "texto": (
            f"Dejé un borrador pendiente para {destino} con asunto «{titulo}». "
            f"Todavía no se guarda ni se envía: al aprobar quedará {donde}. "
            "Pulsa Aprobar o Rechazar."
        ),
        "pendiente": pendiente,
    }


@capturar
def proponer_enviar(borrador_id: str | None = None) -> dict:
    borradores = [item for item in _todos() if not item.get("enviado")]
    if not borradores:
        raise ValueError("No hay borradores. Primero redacta un correo y aprueba ese borrador.")
    if borrador_id:
        elegido = next((item for item in borradores if item.get("id") == borrador_id), None)
    else:
        elegido = borradores[-1]
    if elegido is None:
        raise ValueError("No encontré ese borrador.")
    if elegido.get("origen") != "gmail" or not elegido.get("gmail_id"):
        raise ValueError(
            "Ese borrador está solo en este equipo, así que no puedo enviarlo. "
            "Conecta Gmail, vuelve a redactarlo, aprueba el borrador y después aprueba el envío."
        )
    nota = "Al aprobar se ENVÍA este correo desde Gmail."
    vista = (
        f"Para: {elegido.get('para')}\nAsunto: {elegido.get('asunto')}\n\n"
        f"{elegido.get('cuerpo')}\n\n{nota}"
    )
    pendiente = aprobacion.agregar(
        "enviar_correo",
        "Enviar correo",
        f"{elegido.get('para')} — {elegido.get('asunto')}",
        vista,
        {
            "borrador_id": elegido.get("id"),
            "gmail_id": elegido.get("gmail_id"),
            "para": elegido.get("para"),
            "asunto": elegido.get("asunto"),
            "cuerpo": elegido.get("cuerpo"),
            "nota": nota,
        },
    )
    return {
        "ok": True,
        "texto": (
            f"Preparé el envío a {elegido.get('para')} con asunto «{elegido.get('asunto')}». "
            "Todavía no salió. Pulsa Aprobar y enviar solo si de verdad quieres mandarlo."
        ),
        "pendiente": pendiente,
    }


def _guardar_borrador(accion: dict) -> str:
    detalle = accion["detalle"]
    para = detalle["para"]
    asunto = detalle["asunto"]
    cuerpo = detalle["cuerpo"]
    registro = {
        "id": uuid.uuid4().hex,
        "para": para,
        "asunto": asunto,
        "cuerpo": cuerpo,
        "origen": "local",
        "gmail_id": None,
        "enviado": False,
        "creado": ahora(),
    }
    if _gmail_listo():
        try:
            gmail = servicio("gmail", "v1")
            creado = gmail.users().drafts().create(
                userId="me",
                body={"message": {"raw": _mensaje_raw(para, asunto, cuerpo)}},
            ).execute()
        except Exception as exc:
            raise RuntimeError(mensaje_error_google(exc)) from exc
        registro["origen"] = "gmail"
        registro["gmail_id"] = creado.get("id")
        items = _todos()
        items.append(registro)
        _guardar(items)
        return (
            f"Creé un borrador en Gmail para {para} con asunto «{asunto}». "
            f"No se envió. Id del borrador: {registro['gmail_id']}."
        )
    items = _todos()
    items.append(registro)
    _guardar(items)
    return (
        f"Gmail no está conectado. Guardé el borrador solo en este equipo para {para} "
        f"con asunto «{asunto}». No se envió."
    )


def _enviar(accion: dict) -> str:
    detalle = accion["detalle"]
    gmail_id = detalle.get("gmail_id")
    if not gmail_id:
        raise RuntimeError("Este borrador no está en Gmail, así que no lo envío.")
    try:
        gmail = servicio("gmail", "v1")
        gmail.users().drafts().send(userId="me", body={"id": gmail_id}).execute()
    except Exception as exc:
        raise RuntimeError(mensaje_error_google(exc)) from exc
    items = _todos()
    for item in items:
        if item.get("id") == detalle.get("borrador_id") or item.get("gmail_id") == gmail_id:
            item["enviado"] = True
    _guardar(items)
    return f"Envié el correo a {detalle.get('para')} con asunto «{detalle.get('asunto')}»."


aprobacion.registrar_ejecutor("borrador_correo", _guardar_borrador)
aprobacion.registrar_ejecutor("enviar_correo", _enviar)
