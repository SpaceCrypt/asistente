"""Servidor local del asistente. Solo escucha en este equipo."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from back.agente import cancelar_generacion, chat, editar, estado_modelo, hay_clave, olvidar_ultimo, ultimo_turno
from back.apoyo import ocultar_secreto
from back import ajustes, aprobacion, chats, memoria
from back.config import WEB, asegurar_datos
from back.googleacceso import conectar, estado as estado_google
from back.herramientas import archivos as _archivos  # registra ejecutores
from back.herramientas import calendario as _calendario  # noqa: F401
from back.herramientas import correo
from back.herramientas import tareas
from back.registro import recientes, vaciar as vaciar_registro
from back.rutas import raices_publicas

_ = _archivos


class PeticionChat(BaseModel):
    mensaje: str = Field(min_length=1, max_length=8000)
    chat_id: str | None = None
    stream: bool = False


class PeticionEditar(BaseModel):
    mensaje_id: str = Field(min_length=32, max_length=32)
    texto: str = Field(min_length=1, max_length=8000)
    chat_id: str | None = None
    stream: bool = False


class PeticionFlag(BaseModel):
    valor: bool = True


class PeticionAjuste(BaseModel):
    modelo: str | None = None
    modo: str | None = None


@asynccontextmanager
async def ciclo(_aplicacion):
    asegurar_datos()
    yield


app = FastAPI(
    title="Asistente personal",
    lifespan=ciclo,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(HTTPException)
async def error_http(_peticion, exc: HTTPException):
    detalle = exc.detail if isinstance(exc.detail, str) else "No pude completar la solicitud."
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": detalle})


@app.exception_handler(RequestValidationError)
async def error_validacion(_peticion, _exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"ok": False, "error": "La petición no es válida. Envía un JSON con el campo mensaje."},
    )


@app.exception_handler(Exception)
async def error_general(_peticion, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "Error interno. El servidor sigue activo.",
            "detalle": ocultar_secreto(str(exc)),
        },
    )


def _archivo(nombre: str, tipo: str) -> FileResponse:
    return FileResponse(
        WEB / nombre,
        media_type=tipo,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/")
def inicio():
    return _archivo("index.html", "text/html; charset=utf-8")


@app.get("/estilos.css")
def estilos():
    return _archivo("estilos.css", "text/css; charset=utf-8")


@app.get("/app.js")
def script():
    return _archivo("app.js", "text/javascript; charset=utf-8")


@app.get("/salud")
def salud():
    return {"ok": True, "servicio": "asistente"}


@app.get("/modelos")
def modelos():
    return ajustes.catalogo()


@app.post("/ajustes")
def guardar_ajustes(cuerpo: PeticionAjuste):
    if not (cuerpo.modelo and cuerpo.modelo.strip()) and not (cuerpo.modo and cuerpo.modo.strip()):
        raise HTTPException(status_code=400, detail="Indica un modelo o un modo.")
    try:
        return ajustes.aplicar(cuerpo.modelo, cuerpo.modo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/limpiar/pendientes")
def limpiar_pendientes():
    aprobacion.vaciar()
    return {"ok": True, "pendientes": []}


@app.post("/limpiar/actividad")
def limpiar_actividad():
    vaciar_registro()
    olvidar_ultimo()
    return {"ok": True}


@app.post("/limpiar/aprendido")
def limpiar_aprendido():
    memoria.olvidar()
    return {"ok": True}


def _detalle_modelo(configurado: bool, modelo: str) -> str:
    if not configurado:
        return ""
    base = os.environ.get("ASISTENTE_BASE_URL", "").lower()
    if "127.0.0.1" in base or "localhost" in base:
        return ""
    return "Este modelo consulta un servicio fuera de este equipo."


@app.get("/estado")
def estado():
    clave = hay_clave()
    modelo = ajustes.modelo()
    uso = estado_modelo() if clave else {"en_gpu": None, "linea": "Modelo no disponible."}
    try:
        google = estado_google()
    except Exception as exc:
        google = {"conectado": False, "hay_credentials": False, "detalle": ocultar_secreto(str(exc))}
    try:
        carpetas = raices_publicas()
    except Exception as exc:
        carpetas = []
        google = {"conectado": False, "hay_credentials": False, "detalle": str(exc)}
    return {
        "ok": True,
        "google": google,
        "raices": carpetas,
        "modelo": {
            "configurado": clave,
            "nombre": modelo if clave else None,
            "detalle": _detalle_modelo(clave, modelo),
            "en_gpu": uso.get("en_gpu"),
            "porcentaje": uso.get("porcentaje"),
            "linea": uso.get("linea") or "",
        },
        "ultimo_turno": ultimo_turno(),
        "actividad": recientes(20),
        "borradores_locales": correo.contar(),
        "tareas_locales": tareas.contar(),
    }


def _sse(datos: dict) -> bytes:
    return ("data: " + json.dumps(datos, ensure_ascii=False) + "\n\n").encode("utf-8")


def _trabajo(funcion, cola: asyncio.Queue, loop) -> None:
    def al_evento(evento: dict) -> None:
        try:
            loop.call_soon_threadsafe(cola.put_nowait, evento)
        except Exception:
            pass

    try:
        al_evento({"tipo": "inicio", "modelo": ajustes.modelo()})
        resultado = funcion(al_evento)
        if not isinstance(resultado, dict):
            resultado = {"ok": False, "error": "Respuesta vacía."}
        al_evento({"tipo": "final", **resultado})
    except Exception as exc:
        al_evento({"tipo": "error", "error": ocultar_secreto(str(exc))})
    finally:
        try:
            loop.call_soon_threadsafe(cola.put_nowait, {"tipo": "fin"})
        except Exception:
            pass


async def _fluir(funcion, request: Request):
    cola: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    tarea = asyncio.create_task(asyncio.to_thread(_trabajo, funcion, cola, loop))
    desconectado = False
    try:
        while True:
            if await request.is_disconnected():
                desconectado = True
                break
            try:
                evento = await asyncio.wait_for(cola.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if evento.get("tipo") == "fin":
                break
            yield _sse(evento)
    finally:
        if desconectado and not tarea.done():
            cancelar_generacion()
        await tarea


def _respuesta_stream(funcion, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _fluir(funcion, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/chat")
async def conversar(peticion: PeticionChat, request: Request):
    if peticion.stream:
        return _respuesta_stream(lambda al_evento: chat(peticion.mensaje, peticion.chat_id, al_evento), request)
    try:
        return await asyncio.to_thread(chat, peticion.mensaje, peticion.chat_id, None, "")
    except KeyError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "No encuentro ese chat."})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.post("/chat/detener")
def detener_chat():
    return cancelar_generacion()


@app.post("/chat/editar")
async def editar_mensaje(peticion: PeticionEditar, request: Request):
    if peticion.stream:
        return _respuesta_stream(
            lambda al_evento: editar(peticion.mensaje_id, peticion.texto, peticion.chat_id, al_evento),
            request,
        )
    try:
        return await asyncio.to_thread(editar, peticion.mensaje_id, peticion.texto, peticion.chat_id, None)
    except KeyError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "No encuentro ese mensaje."})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


def _chat_http(funcion):
    try:
        return funcion()
    except KeyError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "No encuentro ese chat."})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.get("/chats")
def listar_chats():
    return chats.resumen()


@app.post("/chats")
def crear_chat():
    return chats.nuevo()


@app.post("/chats/{id_chat}/activar")
def activar_chat(id_chat: str):
    return _chat_http(lambda: chats.activar(id_chat))


@app.post("/chats/{id_chat}/fijar")
def fijar_chat(id_chat: str, peticion: PeticionFlag):
    return _chat_http(lambda: chats.fijar(id_chat, peticion.valor))


@app.post("/chats/{id_chat}/archivar")
def archivar_chat(id_chat: str, peticion: PeticionFlag):
    return _chat_http(lambda: chats.archivar(id_chat, peticion.valor))


@app.delete("/chats/{id_chat}")
def eliminar_chat(id_chat: str):
    def borrar():
        info = chats.eliminar(id_chat)
        aprobacion.retirar(info.get("pendientes") or [])
        aprobacion.retirar_chat(id_chat)
        memoria.quitar_relacionados(info.get("memoria_ids") or [], info.get("turnos") or [])
        return info

    return _chat_http(borrar)


@app.get("/pendientes")
def pendientes():
    return {"ok": True, "pendientes": aprobacion.listar()}


def _decidir(id_accion: str, funcion):
    try:
        return funcion(id_accion)
    except KeyError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Esa acción ya no está pendiente."})
    except Exception as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.post("/pendientes/{id_accion}/aprobar")
def aprobar(id_accion: str):
    return _decidir(id_accion, aprobacion.aprobar)


@app.post("/pendientes/{id_accion}/rechazar")
def rechazar(id_accion: str):
    return _decidir(id_accion, aprobacion.rechazar)


@app.post("/google/conectar")
def conectar_google():
    try:
        return {"ok": True, "mensaje": conectar()}
    except RuntimeError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"No pude conectar Google: {ocultar_secreto(str(exc))}"},
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
