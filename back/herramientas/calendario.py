"""Calendario de Google, solo lectura. Sin credenciales no inventa eventos."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from back.apoyo import capturar
from back.googleacceso import mensaje_error_google, servicio
from back.registro import registrar

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _momento(evento: dict) -> str:
    inicio = evento.get("start") or {}
    valor = inicio.get("dateTime") or inicio.get("date") or ""
    if not valor:
        return "sin fecha"
    if "T" not in valor:
        return f"{valor} (todo el día)"
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return valor
    return f"{DIAS[instante.weekday()]} {instante.day} {MESES[instante.month - 1]} {instante:%H:%M}"


@capturar
def listar_eventos() -> dict:
    try:
        calendario = servicio("calendar", "v3")
    except RuntimeError as exc:
        registrar("consulta", "Calendario no disponible")
        return {"ok": False, "texto": str(exc), "datos": {"eventos": []}}
    try:
        ahora = datetime.now(timezone.utc)
        hasta = ahora + timedelta(days=7)
        respuesta = calendario.events().list(
            calendarId="primary",
            timeMin=ahora.isoformat(),
            timeMax=hasta.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()
    except Exception as exc:
        registrar("consulta", "Error al consultar el calendario")
        return {"ok": False, "texto": mensaje_error_google(exc), "datos": {"eventos": []}}
    eventos = respuesta.get("items") or []
    if not eventos:
        texto = "No hay eventos en los próximos 7 días."
    else:
        lineas = ["Próximos 7 días:"]
        for evento in eventos:
            titulo = evento.get("summary") or "(sin título)"
            donde = evento.get("location")
            extra = f" — {donde}" if donde else ""
            lineas.append(f"- {_momento(evento)} — {titulo}{extra}")
        texto = "\n".join(lineas)
    registrar("consulta", f"Consultó el calendario ({len(eventos)} eventos)")
    return {"ok": True, "texto": texto, "datos": {"eventos": len(eventos)}}
