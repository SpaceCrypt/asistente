"""Tareas de Google si hay conexión; si no, la lista local de este equipo."""

from __future__ import annotations

import uuid

from back import aprobacion
from back.apoyo import capturar, leer_json
from back.apoyo import ahora as marca_tiempo
from back.apoyo import guardar_json
from back.config import DATOS
from back.googleacceso import mensaje_error_google, servicio
from back.registro import registrar

_ARCHIVO = DATOS / "tareas.json"


def _locales() -> list[dict]:
    datos = leer_json(_ARCHIVO, [])
    return datos if isinstance(datos, list) else []


def contar() -> int:
    return len(_locales())


def _texto_locales(tareas: list[dict], encabezado: str) -> str:
    if not tareas:
        cuerpo = "(ninguna todavía)"
    else:
        cuerpo = "\n".join(f"- {tarea.get('titulo') or '(sin título)'}" for tarea in tareas)
    return (
        f"{encabezado}\n{cuerpo}\n\n"
        "Puedes decir: crea una tarea local comprar pan"
    )


@capturar
def listar() -> dict:
    try:
        cliente = servicio("tasks", "v1")
    except RuntimeError as exc:
        tareas = _locales()
        registrar("consulta", f"Tareas locales ({len(tareas)}), Google no conectado")
        return {
            "ok": True,
            "texto": _texto_locales(
                tareas,
                "Google Tasks no está conectado. Estas son las tareas locales de este equipo:",
            ),
            "datos": {"origen": "local", "total": len(tareas)},
        }
    try:
        listas = cliente.tasklists().list(maxResults=10).execute().get("items") or []
        lineas = ["Tareas de Google:"]
        total = 0
        if not listas:
            lineas.append("(no hay listas)")
        for lista in listas:
            lineas.append(f"[{lista.get('title') or 'Lista'}]")
            elementos = cliente.tasks().list(
                tasklist=lista["id"],
                showCompleted=False,
                maxResults=20,
            ).execute().get("items") or []
            if not elementos:
                lineas.append("- (sin tareas abiertas)")
            for elemento in elementos:
                total += 1
                lineas.append(f"- {elemento.get('title') or '(sin título)'}")
    except Exception as exc:
        registrar("consulta", "Error al consultar Google Tasks")
        return {"ok": False, "texto": mensaje_error_google(exc), "datos": {"origen": "google"}}
    locales = _locales()
    if locales:
        lineas.append("")
        lineas.append(f"Además hay {len(locales)} tareas locales en este equipo:")
        for tarea in locales:
            lineas.append(f"- {tarea.get('titulo') or '(sin título)'}")
    registrar("consulta", f"Consultó Google Tasks ({total})")
    return {"ok": True, "texto": "\n".join(lineas), "datos": {"origen": "google", "total": total}}


@capturar
def proponer_crear(titulo: str, notas: str = "") -> dict:
    limpio = (titulo or "").strip()
    if not limpio:
        raise ValueError("La tarea necesita un título.")
    if len(limpio) > 200:
        raise ValueError("El título es demasiado largo.")
    notas_limpias = (notas or "").strip()[:2000]
    vista = f"Tarea local:\n{limpio}"
    if notas_limpias:
        vista += f"\n\nNotas:\n{notas_limpias}"
    vista += (
        "\n\nAl aprobar se guarda en datos/tareas.json de este equipo. "
        "No se crea en Google: el permiso de Tasks es solo de lectura."
    )
    pendiente = aprobacion.agregar(
        "tarea_local",
        "Crear tarea local",
        limpio,
        vista,
        {"titulo": limpio, "notas": notas_limpias},
    )
    return {
        "ok": True,
        "texto": (
            f"Preparé la tarea local «{limpio}». Aún no la guardé. "
            "Pulsa Aprobar o Rechazar. No va a Google Tasks."
        ),
        "pendiente": pendiente,
    }


def _crear(accion: dict) -> str:
    detalle = accion["detalle"]
    titulo = (detalle.get("titulo") or "").strip()
    if not titulo:
        raise ValueError("La tarea no tiene título.")
    tareas = _locales()
    tareas.append(
        {
            "id": uuid.uuid4().hex,
            "titulo": titulo,
            "notas": detalle.get("notas") or "",
            "creado": marca_tiempo(),
            "hecha": False,
        }
    )
    guardar_json(_ARCHIVO, tareas)
    return f"Guardé la tarea local «{titulo}» en este equipo. No se creó en Google."


aprobacion.registrar_ejecutor("tarea_local", _crear)
