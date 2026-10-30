"""Acceso opcional a Google. Sin credentials.json no hay red hacia Google."""

from __future__ import annotations

import json
from pathlib import Path

from back.config import DATOS, PROYECTO, asegurar_datos

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]

INSTRUCCIONES = (
    "Falta credentials.json. En Google Cloud crea un proyecto de prueba, activa "
    "Google Calendar API, Google Tasks API y Gmail API, configura la pantalla de "
    "consentimiento en modo de prueba con tu correo como usuario de prueba, crea un "
    "cliente OAuth de tipo Aplicación de escritorio y guarda el JSON como "
    "credentials.json en la carpeta del proyecto o en datos/credentials.json. "
    "Luego pulsa Conectar Google. El paso a paso está en el README."
)


def ruta_credentials() -> Path | None:
    for candidato in (PROYECTO / "credentials.json", DATOS / "credentials.json"):
        if candidato.is_file():
            return candidato
    return None


def ruta_token() -> Path:
    return DATOS / "token.json"


def _librerias():
    try:
        import httplib2
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_httplib2 import AuthorizedHttp
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Faltan las librerías de Google. Vuelve a ejecutar iniciar.bat."
        ) from exc
    return Credentials, InstalledAppFlow, Request, build, httplib2, AuthorizedHttp


def _validar_cliente(ruta: Path) -> None:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "No pude leer credentials.json. Descarga de nuevo el cliente de escritorio."
        ) from exc
    if not isinstance(datos, dict) or "installed" not in datos:
        raise RuntimeError(
            "credentials.json no es un cliente de escritorio. En Google Cloud crea "
            "un ID de cliente de tipo Aplicación de escritorio y vuelve a descargarlo."
        )


def cargar_credenciales():
    """Devuelve (credenciales o None, mensaje). Nunca abre el navegador."""
    ruta = ruta_credentials()
    if ruta is None:
        return None, INSTRUCCIONES
    token = ruta_token()
    if not token.is_file():
        return None, "Hay credentials.json, pero falta pulsar Conectar Google."
    try:
        Credentials, _, Request, _, _, _ = _librerias()
        credenciales = Credentials.from_authorized_user_file(str(token), SCOPES)
    except Exception:
        return None, "No pude leer datos/token.json. Pulsa Conectar Google otra vez."
    try:
        if credenciales and credenciales.expired and credenciales.refresh_token:
            credenciales.refresh(Request())
            asegurar_datos()
            token.write_text(credenciales.to_json(), encoding="utf-8")
    except Exception:
        return None, "No pude renovar el acceso a Google. Revisa la conexión o vuelve a conectar."
    if not credenciales or not credenciales.valid:
        return None, "La autorización de Google no es válida. Pulsa Conectar Google."
    if not credenciales.has_scopes(SCOPES):
        return None, "El permiso guardado no incluye calendario, tareas y Gmail. Vuelve a conectar."
    return credenciales, "Conectado con Google."


def estado() -> dict:
    hay = ruta_credentials() is not None
    if not hay:
        return {"conectado": False, "hay_credentials": False, "detalle": "Google sin conectar."}
    credenciales, mensaje = cargar_credenciales()
    return {
        "conectado": credenciales is not None,
        "hay_credentials": True,
        "detalle": mensaje,
    }


def conectar() -> str:
    ruta = ruta_credentials()
    if ruta is None:
        raise RuntimeError(INSTRUCCIONES)
    _validar_cliente(ruta)
    credenciales, _ = cargar_credenciales()
    if credenciales is not None:
        return "Google ya está conectado en este equipo."
    _, InstalledAppFlow, _, _, _, _ = _librerias()
    flujo = InstalledAppFlow.from_client_secrets_file(str(ruta), SCOPES)
    try:
        nuevas = flujo.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            timeout_seconds=180,
            access_type="offline",
            prompt="consent",
            authorization_prompt_message="Abre esta dirección para autorizar el asistente:\n{url}",
            success_message="Listo. Ya puedes cerrar esta pestaña y volver al asistente.",
        )
    except Exception as exc:
        raise RuntimeError(f"No se completó la autorización de Google: {exc}") from exc
    asegurar_datos()
    ruta_token().write_text(nuevas.to_json(), encoding="utf-8")
    return "Google quedó conectado. El token está en datos/token.json, solo en este equipo."


def servicio(api: str, version: str):
    credenciales, mensaje = cargar_credenciales()
    if credenciales is None:
        raise RuntimeError(mensaje)
    _, _, _, build, httplib2, AuthorizedHttp = _librerias()
    http = AuthorizedHttp(credenciales, http=httplib2.Http(timeout=30))
    return build(api, version, http=http, cache_discovery=False)


def mensaje_error_google(exc: Exception) -> str:
    texto = str(exc)
    if "invalid_grant" in texto:
        return "La autorización de Google caducó. Pulsa Conectar Google otra vez."
    if "accessNotConfigured" in texto or "has not been used" in texto or "disabled" in texto:
        return "Falta activar la API en Google Cloud (Calendar, Tasks o Gmail)."
    if "403" in texto or "insufficient" in texto.lower():
        return (
            "Google denegó el permiso. Revisa la pantalla de consentimiento, "
            "tu usuario de prueba y los cuatro scopes del README."
        )
    if "401" in texto:
        return "Google no aceptó el token. Vuelve a conectar."
    return f"No pude consultar Google: {texto[:300]}"
