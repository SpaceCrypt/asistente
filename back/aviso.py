"""Aviso de escritorio al crear una acción pendiente. Un toast por acción."""

from __future__ import annotations

import base64
import os
import subprocess

from back.apoyo import ocultar_secreto
from back.registro import registrar

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$bytes = [Convert]::FromBase64String($env:ASISTENTE_TOAST_XML)
$texto = [System.Text.Encoding]::UTF8.GetString($bytes)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($texto)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
$notifier.Show($toast)
"""


def _xml(texto: str) -> str:
    return (
        (texto or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def notificar(titulo: str, cuerpo: str) -> str | None:
    """Muestra un toast de Windows. Devuelve None si salió, o el error."""
    titulo_limpio = " ".join((titulo or "Acción pendiente").split())[:120] or "Acción pendiente"
    cuerpo_limpio = " ".join((cuerpo or "").split())[:220]
    documento = (
        "<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{_xml(titulo_limpio)}</text>"
        f"<text>{_xml(cuerpo_limpio)}</text>"
        "</binding></visual></toast>"
    )
    entorno = os.environ.copy()
    entorno["ASISTENTE_TOAST_XML"] = base64.b64encode(documento.encode("utf-8")).decode("ascii")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        resultado = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            capture_output=True,
            text=True,
            timeout=12,
            env=entorno,
            creationflags=flags,
            check=False,
        )
    except Exception as exc:
        error = ocultar_secreto(str(exc))
        registrar("aviso", f"Falló el toast de «{titulo_limpio}»: {error}")
        return error
    if resultado.returncode != 0:
        detalle = (resultado.stderr or resultado.stdout or "PowerShell no mostró el toast.").strip()
        error = ocultar_secreto(detalle)[:300]
        registrar("aviso", f"Falló el toast de «{titulo_limpio}»: {error}")
        return error
    return None
