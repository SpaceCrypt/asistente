"""Agente con modelo de lenguaje y herramientas. La frase la interpreta el modelo."""

from __future__ import annotations

import contextvars
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request

from back import ajustes, apoyo, chats, memoria, rutas
from back.herramientas import archivos, calendario, correo, tareas

_turno: contextvars.ContextVar[str] = contextvars.ContextVar("turno_asistente", default="")
# El contexto sigue en 2048. Solo RAM no usa la gráfica. Gráfica y RAM usa 12 capas.
# Solo gráfica manda todas las capas (num_gpu alto). No se recorta a 16 en silencio.
# El modelo se suelta a los 30 s.
_CTX_TOPE = 2048
_CAPAS_TOPE = 16
_CAPAS_TODA_LA_GRAFICA = 99
_CAPAS_DEFECTO = 12
_KEEP_ALIVE = "30s"


def hay_clave() -> bool:
    return bool(os.environ.get("ASISTENTE_API_KEY", "").strip())


def solo_texto(texto: str, herramienta: str = "ayuda") -> dict:
    return {
        "ok": True,
        "respuesta": texto,
        "pendientes_nuevos": [],
        "herramientas": [herramienta],
        "modo": "local",
        "datos": {},
    }


HERRAMIENTAS = [
    {
        "type": "function",
        "function": {
            "name": "listar_archivos",
            "description": "Lista una carpeta de esta PC. Se ejecuta al momento y no modifica nada. escritorio es la carpeta Desktop del usuario, no la carpeta del proyecto. También puedes usar perfil, documentos, descargas, proyecto o disco c.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directorio": {"type": "string", "description": "Ruta o alias. Por defecto escritorio."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_archivos",
            "description": (
                "Busca carpetas y archivos por nombre en el Escritorio (la carpeta Desktop) y en el resto de la PC. "
                "No modifica nada. Pasa el nombre que entendiste, aunque tenga espacios. "
                "Si la ruta que armaste no existe, busca antes el nombre de la carpeta y .txt. "
                "Si hay varias coincidencias, el resultado pide preguntar cuál. Si no hay ninguna, lo dice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {
                        "type": "string",
                        "description": "Nombre o fragmento del archivo o de la carpeta, tal como lo entendiste.",
                    }
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leer_archivo",
            "description": "Lee un archivo de texto dentro de las carpetas permitidas. Se ejecuta al momento.",
            "parameters": {
                "type": "object",
                "properties": {"ruta": {"type": "string"}},
                "required": ["ruta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_crear_carpeta",
            "description": (
                "Deja pendiente la creación de una carpeta. No la crea hasta que el usuario apruebe. "
                "La ruta usa un alias: escritorio, documentos, descargas o proyecto. "
                "Ejemplo: escritorio/prueba. Si el archivo va dentro, llama esta herramienta primero."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta de la carpeta. Ejemplo: escritorio/prueba"},
                    "motivo": {"type": "string"},
                },
                "required": ["ruta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_crear_word",
            "description": (
                "Deja pendiente un documento Word .docx real, con el texto dentro. No lo crea hasta que el usuario apruebe. "
                "No sirve para .txt. La ruta termina en .docx y usa alias. "
                "Ejemplo: escritorio/prueba/pruebadelaprueba.docx. "
                "Si piden un texto base de prueba y no dictan el párrafo, redacta tú un párrafo breve en español y pásalo en texto. "
                "Puedes llamarla aunque la carpeta todavía no exista, después de proponer_crear_carpeta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta .docx. Ejemplo: escritorio/prueba/nota.docx"},
                    "texto": {"type": "string", "description": "Párrafo que quedará dentro del documento."},
                    "motivo": {"type": "string"},
                },
                "required": ["ruta", "texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_crear_excel",
            "description": (
                "Deja pendiente un libro Excel .xlsx real, con hojas, columnas y filas en celdas. "
                "Úsala cuando pidan Excel, .xlsx o una tabla de hoja de cálculo. "
                "No la uses para PDF, Word ni .txt. Prohibido simular un Excel con texto plano y extensión .xlsx. "
                "No crea el archivo hasta que el usuario apruebe. "
                "Si el archivo está en el Escritorio, la ruta empieza por escritorio/. "
                "Esa carpeta es el Desktop del usuario, no la carpeta del proyecto. "
                "Si leíste un .txt, el .xlsx queda junto a ese archivo y las celdas salen de su contenido."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta que termina en .xlsx. Ejemplo: pruebas/tabla.xlsx"},
                    "nombre_hoja": {"type": "string", "description": "Nombre de la hoja si solo hay una. Por defecto Datos."},
                    "columnas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Cabecera de la hoja. Cada nombre es una columna.",
                    },
                    "filas": {
                        "type": "array",
                        "description": "Filas de celdas. Cada fila es una lista de valores, en el mismo orden que columnas.",
                        "items": {"type": "array"},
                    },
                    "hojas": {
                        "type": "array",
                        "description": "Varias hojas. Cada una tiene nombre, columnas y filas.",
                        "items": {"type": "object"},
                    },
                    "motivo": {"type": "string"},
                },
                "required": ["ruta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_crear_pdf",
            "description": (
                "Deja pendiente un PDF real, que al abrirse empieza por %PDF, con párrafos legibles. "
                "Úsala cuando pidan un PDF. No la uses para Excel, Word ni .txt. "
                "Prohibido simular un PDF con texto plano o con un Word renombrado. "
                "No crea el archivo hasta que el usuario apruebe. "
                "Ejemplo de ruta: pruebas/nota.pdf."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta que termina en .pdf. Ejemplo: pruebas/nota.pdf"},
                    "texto": {"type": "string", "description": "Párrafos del PDF. Separa los párrafos con una línea en blanco."},
                    "titulo": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["ruta", "texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_escribir",
            "description": (
                "Prepara crear o modificar un archivo de texto (.txt, .md y similares). "
                "No la uses para .docx, .xlsx, .pdf ni para carpetas. No escribe hasta que el usuario apruebe. "
                "Si el archivo está en el Escritorio, la ruta empieza por escritorio/. La carpeta del proyecto solo si te piden el proyecto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string"},
                    "contenido": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["ruta", "contenido"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_borrar",
            "description": "Prepara borrar un archivo. No borra carpetas. No borra hasta que el usuario apruebe.",
            "parameters": {
                "type": "object",
                "properties": {"ruta": {"type": "string"}, "motivo": {"type": "string"}},
                "required": ["ruta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_mover",
            "description": "Prepara mover un archivo dentro de las carpetas permitidas. No mueve hasta que el usuario apruebe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origen": {"type": "string"},
                    "destino": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["origen", "destino"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "redactar_correo",
            "description": "Prepara un borrador de correo. No lo guarda ni lo envía hasta que el usuario apruebe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "para": {"type": "string"},
                    "asunto": {"type": "string"},
                    "cuerpo": {"type": "string"},
                },
                "required": ["para", "asunto", "cuerpo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_enviar_correo",
            "description": "Prepara el envío de un borrador que ya existe. No envía hasta una aprobación distinta.",
            "parameters": {
                "type": "object",
                "properties": {"borrador_id": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_eventos",
            "description": "Lista eventos de Google Calendar de los próximos 7 días. Si no hay conexión, dilo y no inventes eventos.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_tareas",
            "description": "Lista Google Tasks o, si no hay conexión, las tareas locales, diciendo claramente cuál es cuál.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proponer_tarea_local",
            "description": "Prepara una tarea local. No la guarda hasta que el usuario apruebe. No crea tareas en Google.",
            "parameters": {
                "type": "object",
                "properties": {"titulo": {"type": "string"}, "notas": {"type": "string"}},
                "required": ["titulo"],
            },
        },
    },
]


def _cadena(valor) -> str:
    if isinstance(valor, str):
        return valor.strip()
    if isinstance(valor, dict):
        for clave in ("ruta", "directorio", "path", "nombre", "texto", "contenido", "value"):
            interno = valor.get(clave)
            if isinstance(interno, str) and interno.strip():
                return interno.strip()
        for interno in valor.values():
            if isinstance(interno, str) and interno.strip():
                return interno.strip()
    return ""


def _campo(argumentos: dict, *claves: str) -> str:
    for clave in claves:
        if clave in argumentos:
            valor = _cadena(argumentos.get(clave))
            if valor:
                return valor
    return ""


def ejecutar_herramienta(nombre: str, argumentos: dict) -> dict:
    if not isinstance(argumentos, dict):
        argumentos = {}
    if nombre == "listar_archivos":
        return archivos.listar(_campo(argumentos, "directorio", "ruta") or None)
    if nombre == "buscar_archivos":
        return archivos.buscar(_campo(argumentos, "nombre", "consulta", "texto", "ruta"))
    if nombre == "leer_archivo":
        return archivos.leer(_campo(argumentos, "ruta", "archivo"))
    if nombre == "proponer_crear_carpeta":
        return archivos.proponer_crear_carpeta(
            _campo(argumentos, "ruta", "directorio", "carpeta"),
            _campo(argumentos, "motivo"),
        )
    if nombre == "proponer_crear_word":
        return archivos.proponer_crear_word(
            _campo(argumentos, "ruta", "archivo", "path"),
            _campo(argumentos, "texto", "contenido", "parrafo"),
            _campo(argumentos, "motivo"),
        )
    if nombre == "proponer_crear_excel":
        return archivos.proponer_crear_excel(
            _campo(argumentos, "ruta", "archivo", "path"),
            argumentos.get("hojas"),
            argumentos.get("columnas"),
            argumentos.get("filas"),
            _campo(argumentos, "nombre_hoja", "hoja"),
            _campo(argumentos, "motivo"),
            _campo(argumentos, "texto", "contenido", "tabla"),
        )
    if nombre == "proponer_crear_pdf":
        return archivos.proponer_crear_pdf(
            _campo(argumentos, "ruta", "archivo", "path"),
            _campo(argumentos, "texto", "contenido", "parrafo"),
            _campo(argumentos, "titulo"),
            _campo(argumentos, "motivo"),
        )
    if nombre == "proponer_escribir":
        return archivos.proponer_escribir(
            _campo(argumentos, "ruta", "archivo", "path"),
            _campo(argumentos, "contenido", "texto"),
            _campo(argumentos, "motivo"),
        )
    if nombre == "proponer_borrar":
        return archivos.proponer_borrar(_campo(argumentos, "ruta", "archivo"), _campo(argumentos, "motivo"))
    if nombre == "proponer_mover":
        return archivos.proponer_mover(
            _campo(argumentos, "origen", "desde"),
            _campo(argumentos, "destino", "hacia", "ruta"),
            _campo(argumentos, "motivo"),
        )
    if nombre == "redactar_correo":
        return correo.redactar(
            argumentos.get("para") or "",
            argumentos.get("asunto") or "",
            argumentos.get("cuerpo") or "",
        )
    if nombre == "proponer_enviar_correo":
        return correo.proponer_enviar(argumentos.get("borrador_id"))
    if nombre == "listar_eventos":
        return calendario.listar_eventos()
    if nombre == "listar_tareas":
        return tareas.listar()
    if nombre == "proponer_tarea_local":
        return tareas.proponer_crear(argumentos.get("titulo") or "", argumentos.get("notas") or "")
    return {"ok": False, "texto": f"No tengo la herramienta {nombre}."}


def _sistema(mensaje: str = "") -> str:
    recuerdo = memoria.bloque(mensaje)
    memoria_txt = f"\n{recuerdo}\n" if recuerdo else "\n"
    return (
        "Eres el asistente personal de Andres, en su PC con Windows. Respondes en español, claro y breve.\n"
        "Interpretas cualquier redacción. No esperas una frase hecha ni un formato fijo.\n"
        "SÍ puedes acceder a la PC de Andres mediante las herramientas: su perfil de usuario "
        "(Escritorio, Documentos, Descargas y el resto de su carpeta) y los discos locales de esta máquina. "
        "No digas que no tienes acceso al sistema, ni que no puedes entrar en su equipo, ni inventes esa limitación.\n"
        "El Escritorio del usuario es su carpeta Desktop, no la carpeta del proyecto. "
        "No armes una ruta dentro del proyecto salvo que te pidan el proyecto, y no inventes el nombre del archivo.\n"
        "Si la persona pide actuar sobre archivos o carpetas, llama a listar_archivos o a buscar_archivos "
        "antes de decir que algo no existe y antes de preguntar qué hacer cuando la ruta ya está dicha. "
        "Si pega una ruta, lista esa ruta. No inventes el nombre del archivo.\n"
        "Si no encuentras el archivo en la ruta que armaste, usa buscar_archivos antes de decir que no existe. "
        "El argumento es el nombre de la carpeta que entendiste y, si es un texto, .txt, aunque tenga espacios. "
        "Si hay varias coincidencias, preguntas cuál. Si no hay ninguna, lo dices.\n"
        "Leer, listar y buscar se ejecutan al llamarlas. "
        "Escribir, borrar, mover, crear y enviar correo solo quedan pendientes hasta que Andres pulse Aprobar.\n"
        "Si piden pasar un .txt a Excel, búscalo, léelo y llama a proponer_crear_excel. "
        "El .xlsx queda pendiente, con el contenido en celdas, y no se escribe nada hasta Aprobar. "
        "Excel: proponer_crear_excel. PDF: proponer_crear_pdf. Word: proponer_crear_word. "
        "Texto plano: proponer_escribir. No simules un Excel ni un PDF con texto plano.\n"
        "No leas ni muestres .env, credentials.json, token.json ni las contraseñas del navegador.\n"
        "No hay terminal. Si Google no está configurado, dilo y no inventes eventos, tareas ni correos.\n"
        f"{memoria_txt}"
    )


def _base_modelo() -> str:
    return os.environ.get("ASISTENTE_BASE_URL", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1"


def _modelo_local() -> bool:
    base = _base_modelo().lower()
    return "127.0.0.1" in base or "localhost" in base


def _argumentos(crudo) -> dict:
    if isinstance(crudo, dict):
        return crudo
    if not isinstance(crudo, str) or not crudo.strip():
        return {}
    try:
        datos = json.loads(crudo)
    except json.JSONDecodeError:
        return {}
    return datos if isinstance(datos, dict) else {}


def _nombre_llamada(llamada: dict) -> str:
    funcion = llamada.get("function") if isinstance(llamada.get("function"), dict) else {}
    return (funcion.get("name") or llamada.get("name") or "").strip()


def _argumentos_llamada(llamada: dict) -> dict:
    funcion = llamada.get("function") if isinstance(llamada.get("function"), dict) else {}
    crudo = funcion.get("arguments")
    if crudo is None:
        crudo = llamada.get("arguments")
    return _argumentos(crudo)


def _prioridad(llamada: dict) -> int:
    return {
        "proponer_crear_carpeta": 0,
        "proponer_crear_word": 1,
        "proponer_crear_excel": 1,
        "proponer_crear_pdf": 1,
        "proponer_escribir": 2,
    }.get(_nombre_llamada(llamada), 10)


def _llamadas_en_texto(contenido: str) -> list[dict]:
    if not contenido or "tool_call" not in contenido:
        return []
    encontradas = []
    for bloque in re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", contenido, flags=re.DOTALL):
        try:
            datos = json.loads(bloque)
        except json.JSONDecodeError:
            continue
        if isinstance(datos, dict) and datos.get("name"):
            encontradas.append(
                {
                    "id": f"xml-{len(encontradas)}",
                    "type": "function",
                    "function": {"name": datos.get("name"), "arguments": datos.get("arguments") or {}},
                }
            )
    return encontradas


def _raiz_ollama() -> str:
    base = _base_modelo().rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def _nombre_modelo() -> str:
    elegido = ajustes.modelo()
    return elegido or os.environ.get("ASISTENTE_MODELO", "qwen3:8b").strip() or "qwen3:8b"


def _opciones_gpu() -> dict:
    """El contexto no pasa de 2048. Solo gráfica no recorta las capas."""
    ctx = _CTX_TOPE
    crudo_ctx = os.environ.get("ASISTENTE_NUM_CTX", "").strip()
    if crudo_ctx.isdigit():
        ctx = int(crudo_ctx)
    ctx = min(max(ctx, 512), _CTX_TOPE)
    nombre = _nombre_modelo()
    modo = ajustes.modo()
    capas = ajustes.capas_del_modo(modo, nombre)
    if modo == "gpu":
        capas = max(capas, _CAPAS_TODA_LA_GRAFICA)
    else:
        capas = min(max(capas, 0), _CAPAS_TOPE)
    return {"temperature": 0, "num_ctx": ctx, "num_gpu": capas}


def _keep_alive() -> str:
    valor = os.environ.get("ASISTENTE_KEEP_ALIVE", "").strip() or _KEEP_ALIVE
    if valor.endswith(("m", "h")) or valor in {"-1", "0"}:
        return _KEEP_ALIVE
    return valor


def estado_modelo() -> dict:
    nombre = _nombre_modelo()
    if not _modelo_local():
        return {"en_gpu": None, "porcentaje": None, "linea": f"{nombre}. No corre en la GPU de este PC."}
    try:
        solicitud = urllib.request.Request(_raiz_ollama() + "/api/ps", method="GET")
        with urllib.request.urlopen(solicitud, timeout=2) as respuesta:
            datos = json.loads(respuesta.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {"en_gpu": None, "porcentaje": None, "linea": f"{nombre}. No pude comprobar si está en la GPU."}
    modelos = datos.get("models") if isinstance(datos, dict) else None
    if not isinstance(modelos, list):
        modelos = []
    elegido = None
    for item in modelos:
        if not isinstance(item, dict):
            continue
        candidato = str(item.get("model") or item.get("name") or "")
        if candidato == nombre or nombre in candidato:
            elegido = item
            break
    if elegido is None:
        with _ultimo_candado:
            previo = dict(_ultimo_turno)
        modelo_previo = str(previo.get("modelo") or "")
        mismo = bool(modelo_previo) and (modelo_previo == nombre or nombre in modelo_previo or modelo_previo in nombre)
        uso_previo = previo.get("prompt_eval_count") or previo.get("eval_count")
        if mismo and isinstance(uso_previo, (int, float)) and not isinstance(uso_previo, bool) and uso_previo > 0:
            return {"en_gpu": None, "porcentaje": None, "linea": f"{nombre} se usó y ya se soltó."}
        return {"en_gpu": None, "porcentaje": None, "linea": f"{nombre}. Aún no está cargado."}
    try:
        size = int(elegido.get("size") or 0)
        vram = int(elegido.get("size_vram") or 0)
    except (TypeError, ValueError):
        size, vram = 0, 0
    if size > 0 and vram > 0:
        porcentaje = int(round(100 * vram / size))
        if porcentaje >= 100:
            return {"en_gpu": True, "porcentaje": 100, "linea": f"{nombre} en GPU (100% GPU)."}
        return {"en_gpu": False, "porcentaje": porcentaje, "linea": f"{nombre} en GPU y CPU ({porcentaje}% GPU)."}
    if vram <= 0:
        return {"en_gpu": False, "porcentaje": 0, "linea": f"{nombre} en CPU."}
    return {"en_gpu": False, "porcentaje": None, "linea": f"{nombre} en GPU y CPU."}


class GeneracionDetenida(Exception):
    """El usuario cerró la petición al modelo."""


class _ConexionModelo:
    def __init__(self) -> None:
        self.cancelar = threading.Event()
        self.respuesta = None
        self.http = None
        self.candado = threading.Lock()


_conexion = _ConexionModelo()
_ultimo_turno: dict = {
    "modelo": None,
    "prompt_eval_count": None,
    "eval_count": None,
    "tokens_por_segundo": None,
    "segundos": None,
    "en_gpu": None,
    "gpu": None,
    "detenido": False,
}
_ultimo_candado = threading.Lock()
_CLAVES_USO = ("prompt_eval_count", "eval_count", "eval_duration", "prompt_eval_duration", "total_duration")


def _cortar_respuesta(respuesta) -> None:
    if respuesta is None:
        return
    try:
        fp = getattr(respuesta, "fp", None)
        raw = getattr(fp, "raw", None) if fp is not None else None
        sock = getattr(raw, "_sock", None) if raw is not None else None
        if sock is None and raw is not None:
            sock = getattr(raw, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        respuesta.close()
    except Exception:
        pass


def empezar_generacion() -> threading.Event:
    with _conexion.candado:
        _conexion.cancelar.set()
        anterior = _conexion.respuesta
        anterior_http = _conexion.http
        evento = threading.Event()
        _conexion.cancelar = evento
        _conexion.respuesta = None
        _conexion.http = None
    _cortar_respuesta(anterior)
    _cerrar_http(anterior_http)
    return evento


def _cerrar_http(conexion) -> None:
    if conexion is None:
        return
    try:
        sock = getattr(conexion, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        conexion.close()
    except Exception:
        pass


def _soltar_modelo_en_curso() -> None:
    """Pide a Ollama que suelte el modelo ya cargado, sin un prompt y sin cargar otro."""
    try:
        from back.ajustes import modelo, soltar_si_cargado

        soltar_si_cargado(modelo())
    except Exception:
        pass


def cancelar_generacion() -> dict:
    with _conexion.candado:
        _conexion.cancelar.set()
        respuesta = _conexion.respuesta
        conexion = _conexion.http
    _cortar_respuesta(respuesta)
    _cerrar_http(conexion)
    threading.Thread(target=_soltar_modelo_en_curso, daemon=True).start()
    return {"ok": True, "estado": "detenido"}


def _cancelado(local: threading.Event | None) -> bool:
    if local is not None and local.is_set():
        return True
    with _conexion.candado:
        return _conexion.cancelar.is_set()


def _registrar_respuesta(respuesta, local: threading.Event | None) -> None:
    with _conexion.candado:
        vigente = local is not None and _conexion.cancelar is local
        if vigente:
            _conexion.respuesta = respuesta
    if local is not None and (not vigente or local.is_set()):
        _cortar_respuesta(respuesta)
        raise GeneracionDetenida()


def _afinar_socket(respuesta) -> None:
    """No cortes la lectura cada segundo.

    En Solo gráfica el primer token puede tardar más de un segundo. Un timeout de 1 s
    interrumpe el cuerpo HTTP y la siguiente lectura revienta; eso no significa que
    Ollama esté apagado. Detener cierra el socket desde otro hilo.
    """
    try:
        raw = respuesta.fp.raw
        sock = getattr(raw, "_sock", None) or getattr(raw, "sock", None)
        if sock is not None:
            sock.settimeout(240)
    except Exception:
        pass


def _extraer_uso(evento: dict) -> dict:
    uso = {}
    if not isinstance(evento, dict):
        return uso
    for clave in _CLAVES_USO:
        valor = evento.get(clave)
        if isinstance(valor, bool):
            continue
        if isinstance(valor, (int, float)):
            uso[clave] = valor
    return uso


def _combinar_uso(partes: list[dict]) -> dict:
    if not partes:
        return {}
    salida = {}
    for clave in _CLAVES_USO:
        if all(clave in parte for parte in partes):
            salida[clave] = sum(parte[clave] for parte in partes)
    duracion = salida.get("eval_duration")
    cuenta = salida.get("eval_count")
    if isinstance(cuenta, (int, float)) and isinstance(duracion, (int, float)) and duracion > 0:
        salida["tokens_por_segundo"] = round(cuenta / (duracion / 1_000_000_000), 2)
    return salida


def _uso_publico(uso: dict | None, segundos: float | None = None) -> dict:
    origen = uso or {}
    return {
        "prompt_eval_count": origen["prompt_eval_count"] if "prompt_eval_count" in origen else None,
        "eval_count": origen["eval_count"] if "eval_count" in origen else None,
        "tokens_por_segundo": origen["tokens_por_segundo"] if "tokens_por_segundo" in origen else None,
        "segundos": round(float(segundos), 2) if isinstance(segundos, (int, float)) and not isinstance(segundos, bool) else None,
    }


def ultimo_turno() -> dict:
    with _ultimo_candado:
        return dict(_ultimo_turno)


def olvidar_ultimo() -> None:
    with _ultimo_candado:
        _ultimo_turno.clear()
    try:
        from back.apoyo import guardar_json
        from back.config import DATOS

        guardar_json(DATOS / "uso.json", {})
    except Exception:
        pass


def _guardar_ultimo(uso: dict, detenido: bool) -> None:
    gpu = estado_modelo()
    datos = {
        "modelo": _nombre_modelo(),
        "prompt_eval_count": uso.get("prompt_eval_count") if "prompt_eval_count" in (uso or {}) else None,
        "eval_count": uso.get("eval_count") if "eval_count" in (uso or {}) else None,
        "tokens_por_segundo": uso.get("tokens_por_segundo") if "tokens_por_segundo" in (uso or {}) else None,
        "segundos": uso.get("segundos"),
        "en_gpu": gpu.get("en_gpu"),
        "gpu": gpu.get("linea"),
        "detenido": detenido,
    }
    with _ultimo_candado:
        _ultimo_turno.clear()
        _ultimo_turno.update(datos)
    try:
        from back.apoyo import guardar_json
        from back.config import DATOS

        guardar_json(DATOS / "uso.json", datos)
    except Exception:
        pass


def _cargar_ultimo() -> None:
    try:
        from back.apoyo import leer_json
        from back.config import DATOS

        datos = leer_json(DATOS / "uso.json", {})
        if isinstance(datos, dict) and datos.get("modelo"):
            with _ultimo_candado:
                _ultimo_turno.update(datos)
    except Exception:
        pass


_cargar_ultimo()


def _llamada_api(indice: int, acum: dict) -> dict:
    argumentos = acum.get("arguments") or ""
    if not isinstance(argumentos, str):
        argumentos = json.dumps(argumentos, ensure_ascii=False)
    return {
        "id": acum.get("id") or f"llamada-{indice}",
        "type": "function",
        "function": {"name": acum.get("name") or "", "arguments": argumentos},
    }


def _emitir_llamada(indice: int, llamada: dict, al_llamada, emitidos: set[int]) -> None:
    if al_llamada is None or indice in emitidos or not isinstance(llamada, dict):
        return
    emitidos.add(indice)
    al_llamada(indice, llamada)


def _fusionar_tool(acumuladas: dict[int, dict], tc: dict) -> int:
    indice = tc.get("index", 0)
    if not isinstance(indice, int):
        indice = 0
    acum = acumuladas.setdefault(indice, {"id": "", "name": "", "arguments": ""})
    if tc.get("id"):
        acum["id"] = str(tc["id"])
    funcion = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    if funcion.get("name"):
        acum["name"] += str(funcion["name"])
    if funcion.get("arguments"):
        trozo = funcion["arguments"]
        if isinstance(trozo, dict):
            acum["arguments"] = json.dumps(trozo, ensure_ascii=False)
        else:
            acum["arguments"] += str(trozo)
    return indice


def _cerrar_anteriores(hasta: int, acumuladas: dict[int, dict], al_llamada, emitidos: set[int]) -> None:
    for indice in sorted(acumuladas):
        if indice < hasta:
            _emitir_llamada(indice, _llamada_api(indice, acumuladas[indice]), al_llamada, emitidos)


def _aplicar_llamadas(llamadas: list, al_llamada, emitidos: set[int] | None = None) -> None:
    hechos = emitidos if emitidos is not None else set()
    orden = sorted(enumerate(llamadas), key=lambda par: (_prioridad(par[1]) if isinstance(par[1], dict) else 10, par[0]))
    for indice, llamada in orden:
        _emitir_llamada(indice, llamada, al_llamada, hechos)


def _desde_json(crudo: dict, al_llamada) -> dict:
    opcion = (crudo.get("choices") or [{}])[0].get("message") or {}
    if not isinstance(opcion, dict):
        opcion = {}
    llamadas = opcion.get("tool_calls") or []
    if not isinstance(llamadas, list):
        llamadas = []
    if not llamadas:
        llamadas = _llamadas_en_texto(opcion.get("content") or "")
        if llamadas:
            opcion = dict(opcion)
            opcion["tool_calls"] = llamadas
            crudo = {"choices": [{"message": opcion}]}
    _aplicar_llamadas(llamadas, al_llamada)
    return crudo


def _leer_sse(respuesta, al_llamada) -> dict:
    contenido: list[str] = []
    acumuladas: dict[int, dict] = {}
    emitidos: set[int] = set()
    vio_nativas = False
    xml_emitidos = 0

    def emitir_xml() -> None:
        nonlocal xml_emitidos
        if vio_nativas or al_llamada is None:
            return
        encontradas = _llamadas_en_texto("".join(contenido))
        while xml_emitidos < len(encontradas):
            _emitir_llamada(xml_emitidos, encontradas[xml_emitidos], al_llamada, emitidos)
            xml_emitidos += 1

    while True:
        crudo = respuesta.readline()
        if not crudo:
            break
        linea = crudo.decode("utf-8", errors="replace").strip()
        if not linea.startswith("data:"):
            if linea.startswith("{") and not contenido and not acumuladas:
                try:
                    return _desde_json(json.loads(linea), al_llamada)
                except json.JSONDecodeError:
                    pass
            continue
        datos = linea[5:].strip()
        if datos == "[DONE]":
            break
        try:
            evento = json.loads(datos)
        except json.JSONDecodeError:
            continue
        choice = (evento.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if delta.get("content"):
            contenido.append(str(delta["content"]))
            emitir_xml()
        nativas = delta.get("tool_calls") or []
        if isinstance(nativas, list) and nativas:
            vio_nativas = True
            for tc in nativas:
                if isinstance(tc, dict):
                    indice = _fusionar_tool(acumuladas, tc)
                    _cerrar_anteriores(indice, acumuladas, al_llamada, emitidos)
        mensaje = choice.get("message") if isinstance(choice.get("message"), dict) else None
        if mensaje:
            if mensaje.get("content"):
                contenido.append(str(mensaje["content"]))
            for tc in mensaje.get("tool_calls") or []:
                if isinstance(tc, dict):
                    vio_nativas = True
                    _fusionar_tool(acumuladas, tc)
    texto = "".join(contenido).strip()
    if vio_nativas:
        resto = [indice for indice in sorted(acumuladas) if indice not in emitidos]
        resto.sort(key=lambda indice: (_prioridad(_llamada_api(indice, acumuladas[indice])), indice))
        for indice in resto:
            _emitir_llamada(indice, _llamada_api(indice, acumuladas[indice]), al_llamada, emitidos)
        llamadas = [_llamada_api(indice, acumuladas[indice]) for indice in sorted(acumuladas)]
    else:
        emitir_xml()
        llamadas = _llamadas_en_texto(texto)
        for indice, llamada in enumerate(llamadas):
            _emitir_llamada(indice, llamada, al_llamada, emitidos)
    mensaje_final: dict = {"role": "assistant", "content": texto or None}
    if llamadas:
        mensaje_final["tool_calls"] = llamadas
    return {"choices": [{"message": mensaje_final}]}


def _mensaje_nativo(mensaje: dict) -> dict:
    salida = {"role": mensaje.get("role") or "user", "content": mensaje.get("content") or ""}
    llamadas = mensaje.get("tool_calls")
    if not isinstance(llamadas, list):
        return salida
    nativas = []
    for llamada in llamadas:
        if not isinstance(llamada, dict):
            continue
        funcion = llamada.get("function") if isinstance(llamada.get("function"), dict) else {}
        argumentos = funcion.get("arguments")
        if isinstance(argumentos, str):
            try:
                argumentos = json.loads(argumentos) if argumentos.strip() else {}
            except json.JSONDecodeError:
                argumentos = {}
        if not isinstance(argumentos, dict):
            argumentos = {}
        nativas.append({"function": {"name": funcion.get("name") or "", "arguments": argumentos}})
    if nativas:
        salida["tool_calls"] = nativas
    return salida


def _llamada_nativa(tc: dict, indice: int) -> dict:
    funcion = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    argumentos = funcion.get("arguments")
    if not isinstance(argumentos, str):
        argumentos = json.dumps(argumentos or {}, ensure_ascii=False)
    return {
        "id": tc.get("id") or f"llamada-{indice}",
        "type": "function",
        "function": {"name": funcion.get("name") or "", "arguments": argumentos},
    }


def _cadena_excepcion(exc: BaseException) -> str:
    partes = []
    actual: BaseException | None = exc
    vistos: set[int] = set()
    while actual is not None and id(actual) not in vistos:
        vistos.add(id(actual))
        partes.append(str(actual))
        actual = actual.__cause__ or actual.__context__
    return " ".join(partes).casefold()


def _ollama_no_responde(exc: BaseException) -> bool:
    actual: BaseException | None = exc
    vistos: set[int] = set()
    while actual is not None and id(actual) not in vistos:
        vistos.add(id(actual))
        if isinstance(actual, ConnectionRefusedError):
            return True
        actual = actual.__cause__ or actual.__context__
    texto = _cadena_excepcion(exc)
    return any(
        marca in texto
        for marca in (
            "10061",
            "connection refused",
            "denegó expresamente",
            "activamente rehusó",
            "no se puede establecer una conexión",
        )
    )


def _conexion_cortada(exc: BaseException) -> bool:
    import http.client

    actual: BaseException | None = exc
    vistos: set[int] = set()
    while actual is not None and id(actual) not in vistos:
        vistos.add(id(actual))
        if isinstance(actual, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, http.client.IncompleteRead)):
            return True
        actual = actual.__cause__ or actual.__context__
    texto = _cadena_excepcion(exc)
    return any(marca in texto for marca in ("10054", "10053", "remote end closed", "incompleteread", "broken pipe"))


def _frase_del_fallo(exc: BaseException) -> str:
    """«El modelo no está disponible» solo si Ollama no acepta la conexión."""
    if _ollama_no_responde(exc):
        return "El modelo no está disponible."
    if isinstance(exc, TimeoutError) or "tardó demasiado" in _cadena_excepcion(exc) or "timed out" in _cadena_excepcion(exc):
        return "El modelo tardó demasiado."
    if _conexion_cortada(exc):
        return "Se cortó la conexión con el modelo."
    motivo = apoyo.ocultar_secreto(str(exc)).strip().splitlines()
    linea = " ".join((motivo[0] if motivo else "").split())
    if len(linea) > 220:
        linea = linea[:220].rstrip() + "…"
    if not linea:
        return "No pude completar la respuesta del modelo."
    if linea[-1] not in ".!?":
        linea += "."
    return linea


def _leer_nativo(respuesta, al_llamada, cancelar=None, al_evento=None) -> tuple[dict, dict]:
    contenido: list[str] = []
    acumuladas: list[dict] = []
    emitidos: set[int] = set()
    uso: dict = {}
    limite = time.monotonic() + 240
    while True:
        if _cancelado(cancelar):
            _cortar_respuesta(respuesta)
            raise GeneracionDetenida()
        if time.monotonic() > limite:
            _cortar_respuesta(respuesta)
            raise TimeoutError("El modelo tardó demasiado.")
        try:
            crudo = respuesta.readline()
        except TimeoutError as exc:
            raise TimeoutError("El modelo tardó demasiado.") from exc
        except Exception as exc:
            if _cancelado(cancelar):
                raise GeneracionDetenida() from exc
            if _conexion_cortada(exc):
                raise RuntimeError("Se cortó la conexión con el modelo.") from exc
            raise
        if _cancelado(cancelar):
            _cortar_respuesta(respuesta)
            raise GeneracionDetenida()
        if not crudo:
            break
        linea = crudo.decode("utf-8", errors="replace").strip()
        if not linea:
            continue
        try:
            evento = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(evento.get("error"), str) and evento.get("error"):
            raise RuntimeError(evento["error"])
        mensaje = evento.get("message") if isinstance(evento.get("message"), dict) else {}
        delta = ""
        if mensaje.get("content"):
            delta = str(mensaje["content"])
            contenido.append(delta)
        hubo_tool = False
        nativas = mensaje.get("tool_calls") or []
        if isinstance(nativas, list) and nativas:
            hubo_tool = True
            for indice, tc in enumerate(nativas):
                if not isinstance(tc, dict):
                    continue
                llamada = _llamada_nativa(tc, indice)
                if indice < len(acumuladas):
                    acumuladas[indice] = llamada
                    continue
                for previo in range(len(acumuladas)):
                    _emitir_llamada(previo, acumuladas[previo], al_llamada, emitidos)
                acumuladas.append(llamada)
        trozo = _extraer_uso(evento)
        if trozo:
            uso.update(trozo)
        if al_evento and (delta or hubo_tool or evento.get("done") or trozo):
            al_evento(
                {
                    "tipo": "modelo",
                    "delta": delta,
                    "herramienta": hubo_tool,
                    "done": bool(evento.get("done")),
                    "uso": _uso_publico(_combinar_uso([uso]) if uso else {}),
                }
            )
        if evento.get("done"):
            break
    for indice, llamada in enumerate(acumuladas):
        _emitir_llamada(indice, llamada, al_llamada, emitidos)
    texto = "".join(contenido).strip()
    if not acumuladas:
        encontradas = _llamadas_en_texto(texto)
        for indice, llamada in enumerate(encontradas):
            _emitir_llamada(indice, llamada, al_llamada, emitidos)
        acumuladas = encontradas
    mensaje_final: dict = {"role": "assistant", "content": texto or None}
    if acumuladas:
        mensaje_final["tool_calls"] = acumuladas
    return {"choices": [{"message": mensaje_final}]}, uso


def _abrir_ollama(cuerpo: bytes, cancelar):
    """Conexión propia: Detener cierra este socket aunque la lectura siga bloqueada."""
    import http.client
    from urllib.parse import urlparse

    partes = urlparse(_raiz_ollama())
    host = partes.hostname or "127.0.0.1"
    puerto = partes.port or 11434
    conexion = http.client.HTTPConnection(host, puerto, timeout=240)
    conexion.connect()
    with _conexion.candado:
        if cancelar is not None and _conexion.cancelar is cancelar:
            _conexion.http = conexion
    if _cancelado(cancelar):
        _cerrar_http(conexion)
        raise GeneracionDetenida()
    try:
        conexion.request(
            "POST",
            "/api/chat",
            body=cuerpo,
            headers={"Content-Type": "application/json"},
        )
        if _cancelado(cancelar):
            _cerrar_http(conexion)
            raise GeneracionDetenida()
        return conexion.getresponse()
    except GeneracionDetenida:
        raise
    except Exception:
        if _cancelado(cancelar):
            _cerrar_http(conexion)
            raise GeneracionDetenida()
        raise


def _pedir_ollama(mensajes: list[dict], al_llamada, stream: bool = True, cancelar=None, al_evento=None) -> tuple[dict, dict]:
    if _cancelado(cancelar):
        raise GeneracionDetenida()
    modelo = _nombre_modelo()
    cuerpo = {
        "model": modelo,
        "messages": [_mensaje_nativo(mensaje) for mensaje in mensajes],
        "tools": HERRAMIENTAS,
        "stream": stream,
        "think": False,
        "options": _opciones_gpu(),
        "keep_alive": _keep_alive(),
    }
    respuesta = None
    try:
        respuesta = _abrir_ollama(json.dumps(cuerpo).encode("utf-8"), cancelar)
        _registrar_respuesta(respuesta, cancelar)
        _afinar_socket(respuesta)
        if stream:
            return _leer_nativo(respuesta, al_llamada, cancelar, al_evento)
        datos = json.loads(respuesta.read().decode("utf-8"))
    except GeneracionDetenida:
        raise
    except urllib.error.HTTPError as exc:
        if _cancelado(cancelar):
            raise GeneracionDetenida() from exc
        detalle = exc.read().decode("utf-8", errors="replace")
        if stream:
            return _pedir_ollama(mensajes, al_llamada, stream=False, cancelar=cancelar, al_evento=al_evento)
        raise RuntimeError(f"El modelo respondió {exc.code}: {apoyo.ocultar_secreto(detalle)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if _cancelado(cancelar):
            raise GeneracionDetenida() from exc
        raise RuntimeError(f"No hay conexión con el modelo: {exc}") from exc
    finally:
        with _conexion.candado:
            if respuesta is not None and _conexion.respuesta is respuesta:
                _conexion.respuesta = None
            conexion = _conexion.http
            _conexion.http = None
        _cortar_respuesta(respuesta)
        _cerrar_http(conexion)
    if _cancelado(cancelar):
        raise GeneracionDetenida()
    uso = _extraer_uso(datos if isinstance(datos, dict) else {})
    mensaje = datos.get("message") if isinstance(datos, dict) else None
    if not isinstance(mensaje, dict):
        raise RuntimeError("El modelo no devolvió una respuesta legible.")
    if al_evento:
        al_evento({"tipo": "modelo", "done": True, "delta": str(mensaje.get("content") or ""), "uso": _uso_publico(_combinar_uso([uso]) if uso else {})})
    return _leer_nativo_desde({"message": mensaje, "done": True}, al_llamada), uso


def _leer_nativo_desde(evento: dict, al_llamada) -> dict:
    mensaje = evento.get("message") if isinstance(evento.get("message"), dict) else {}
    acumuladas = []
    for indice, tc in enumerate(mensaje.get("tool_calls") or []):
        if isinstance(tc, dict):
            acumuladas.append(_llamada_nativa(tc, indice))
    emitidos: set[int] = set()
    for indice, llamada in enumerate(acumuladas):
        _emitir_llamada(indice, llamada, al_llamada, emitidos)
    texto = str(mensaje.get("content") or "").strip()
    if not acumuladas:
        acumuladas = _llamadas_en_texto(texto)
        for indice, llamada in enumerate(acumuladas):
            _emitir_llamada(indice, llamada, al_llamada, emitidos)
    mensaje_final: dict = {"role": "assistant", "content": texto or None}
    if acumuladas:
        mensaje_final["tool_calls"] = acumuladas
    return {"choices": [{"message": mensaje_final}]}


def _pedir_modelo(
    mensajes: list[dict],
    compatible: bool = True,
    forzar: bool = False,
    al_llamada=None,
    stream: bool = True,
    cancelar=None,
    al_evento=None,
) -> tuple[dict, dict]:
    if _cancelado(cancelar):
        raise GeneracionDetenida()
    if _modelo_local():
        return _pedir_ollama(mensajes, al_llamada, cancelar=cancelar, al_evento=al_evento)
    clave = os.environ.get("ASISTENTE_API_KEY", "").strip()
    modelo = _nombre_modelo()
    cuerpo_dict: dict = {
        "model": modelo,
        "messages": mensajes,
        "tools": HERRAMIENTAS,
        "temperature": 0,
        "stream": stream,
    }
    if compatible:
        cuerpo_dict["tool_choice"] = "required" if forzar else "auto"
    if _modelo_local():
        if compatible:
            cuerpo_dict["think"] = False
        cuerpo_dict["options"] = _opciones_gpu()
        cuerpo_dict["keep_alive"] = _keep_alive()
    solicitud = urllib.request.Request(
        _base_modelo().rstrip("/") + "/chat/completions",
        data=json.dumps(cuerpo_dict).encode("utf-8"),
        headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(solicitud, timeout=240 if _modelo_local() else 90) as respuesta:
            if stream:
                return _leer_sse(respuesta, al_llamada), {}
            return _desde_json(json.loads(respuesta.read().decode("utf-8")), al_llamada), {}
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400 and (compatible or stream):
            return _pedir_modelo(
                mensajes,
                compatible=False,
                forzar=False,
                al_llamada=al_llamada,
                stream=False,
                cancelar=cancelar,
                al_evento=al_evento,
            )
        raise RuntimeError(f"El modelo respondió {exc.code}: {apoyo.ocultar_secreto(detalle)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No hay conexión con el modelo: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("El modelo no devolvió una respuesta legible.") from exc


def _empaquetar(respuesta: str, pendientes: list, usadas: list, datos: dict) -> dict:
    if len(pendientes) > 1 and "primero" not in respuesta.lower():
        respuesta += (
            "\n\nDejé varias acciones en este orden: primero la carpeta y después el documento. "
            "Nada se ha ejecutado hasta que pulses Aprobar."
        )
    elif pendientes and "aprobar" not in respuesta.lower():
        respuesta += "\n\nQuedó pendiente. Nada se ha ejecutado: pulsa Aprobar o Rechazar."
    return {
        "ok": True,
        "respuesta": respuesta,
        "pendientes_nuevos": pendientes,
        "herramientas": usadas,
        "modo": "modelo",
        "datos": datos,
    }


def _resultado_tool(resultado: dict) -> str:
    pendiente = resultado.get("pendiente") if isinstance(resultado.get("pendiente"), dict) else {}
    return json.dumps(
        {
            "ok": resultado.get("ok", False),
            "texto": (resultado.get("texto") or "")[:4000],
            "dejo_pendiente": bool(pendiente),
            "ruta": pendiente.get("resumen"),
        },
        ensure_ascii=False,
    )


def _empaquetar_uso(respuesta: str, pendientes: list, usadas: list, datos: dict, partes: list[dict], detenido: bool = False) -> dict:
    empaquetado = _empaquetar(respuesta, pendientes, usadas, datos)
    empaquetado["uso"] = _uso_publico(_combinar_uso(partes))
    if detenido:
        empaquetado["detenido"] = True
        empaquetado["modo"] = "detenido"
    return empaquetado


_EXTENSIONES_ARCHIVO = (".txt", ".xlsx", ".xls", ".docx", ".pdf", ".csv", ".md")


def _rutas_absolutas(texto: str) -> list[str]:
    """Rutas de Windows que la persona pegó. No interpreta una frase."""
    halladas: list[str] = []
    for coincidencia in re.finditer(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*", texto or ""):
        ruta = coincidencia.group(0).strip().rstrip(".,;:)")
        if len(ruta) > 3 and ruta not in halladas:
            halladas.append(ruta)
    return halladas


def _pide_archivos(texto: str) -> bool:
    if _rutas_absolutas(texto):
        return True
    bajo = (texto or "").casefold()
    return any(sufijo in bajo for sufijo in _EXTENSIONES_ARCHIVO)


def _completar_rutas_pegadas(mensaje: str, textos: list[str], pendientes: list, usadas: list[str], datos: dict) -> None:
    """Si el modelo no llamó herramientas, lista la ruta absoluta que sí está escrita."""
    if usadas:
        return
    rutas = _rutas_absolutas(mensaje)
    if not rutas:
        return
    pide_xlsx = ".xlsx" in mensaje.casefold() or ".xls" in mensaje.casefold()
    from pathlib import Path

    for ruta in rutas:
        resultado = archivos.listar(ruta)
        usadas.append("listar_archivos")
        if resultado.get("texto"):
            textos.append(str(resultado["texto"]))
        datos_lista = resultado.get("datos") if isinstance(resultado.get("datos"), dict) else {}
        if isinstance(datos_lista, dict):
            datos.update(datos_lista)
        if not resultado.get("ok") or not pide_xlsx:
            continue
        base = str(datos_lista.get("ruta") or "")
        nombres = datos_lista.get("entradas") or []
        txts = [str(nombre) for nombre in nombres if str(nombre).casefold().endswith(".txt")]
        if len(txts) != 1 or not base:
            continue
        propuesta = archivos.proponer_crear_excel(str(Path(base) / txts[0]), motivo="Pasar el texto a Excel")
        usadas.append("proponer_crear_excel")
        if isinstance(propuesta.get("pendiente"), dict):
            pendientes.append(propuesta["pendiente"])
        if propuesta.get("texto"):
            textos.append(str(propuesta["texto"]))
        if isinstance(propuesta.get("datos"), dict):
            datos.update(propuesta["datos"])


def chat_modelo(mensaje: str, cancelar=None, al_evento=None) -> dict:
    previos = chats.mensajes_modelo(limite=16)
    mensajes: list[dict] = [{"role": "system", "content": _sistema(mensaje)}, *previos, {"role": "user", "content": mensaje}]
    pendientes: list[dict] = []
    textos: list[str] = []
    usadas: list[str] = []
    datos: dict = {}
    partes_uso: list[dict] = []
    reintento = False
    for _ in range(6):
        if _cancelado(cancelar):
            raise GeneracionDetenida()
        hechos: dict[int, dict] = {}

        def al_llamada(indice: int, llamada: dict, _hechos: dict[int, dict] = hechos) -> None:
            if _cancelado(cancelar):
                raise GeneracionDetenida()
            if not isinstance(llamada, dict) or indice in _hechos:
                return
            nombre = _nombre_llamada(llamada)
            resultado = ejecutar_herramienta(nombre, _argumentos_llamada(llamada))
            usadas.append(nombre)
            if isinstance(resultado.get("pendiente"), dict):
                pendientes.append(resultado["pendiente"])
            if resultado.get("texto"):
                textos.append(str(resultado["texto"]))
            if isinstance(resultado.get("datos"), dict):
                datos.update(resultado["datos"])
            _hechos[indice] = resultado

        try:
            crudo, uso_llamada = _pedir_modelo(
                mensajes,
                al_llamada=al_llamada,
                cancelar=cancelar,
                al_evento=al_evento,
            )
        except GeneracionDetenida:
            texto = "Detenido." if not textos else "Detenido.\n\n" + "\n\n".join(textos)
            return _empaquetar_uso(texto, pendientes, usadas, datos, partes_uso, detenido=True)
        if uso_llamada:
            partes_uso.append(uso_llamada)
        opcion = (crudo.get("choices") or [{}])[0].get("message") or {}
        llamadas = opcion.get("tool_calls") or []
        if not isinstance(llamadas, list):
            llamadas = []
        if not llamadas:
            llamadas = _llamadas_en_texto(opcion.get("content") or "")
        if not llamadas:
            if not usadas and not reintento and _pide_archivos(mensaje):
                reintento = True
                mensajes.append({"role": "assistant", "content": opcion.get("content") or ""})
                mensajes.append(
                    {
                        "role": "user",
                        "content": (
                            "No llamaste a ninguna herramienta. Antes de responder, llama a listar_archivos "
                            "o a buscar_archivos con la ruta o el nombre que entendiste. "
                            "No digas que un archivo no existe y no preguntes qué hacer si la ruta ya está dicha. "
                            "No inventes el nombre del archivo. "
                            "Si hay un .txt y piden .xlsx, léelo y llama a proponer_crear_excel."
                        ),
                    }
                )
                continue
            if not usadas:
                _completar_rutas_pegadas(mensaje, textos, pendientes, usadas, datos)
            if usadas and textos:
                respuesta = "\n\n".join(textos)
            else:
                respuesta = (opcion.get("content") or "").strip() or "\n\n".join(textos) or "No obtuve una respuesta del modelo."
            return _empaquetar_uso(respuesta, pendientes, usadas, datos, partes_uso)
        mensajes.append({"role": "assistant", "content": opcion.get("content"), "tool_calls": llamadas})
        orden = sorted(enumerate(llamadas), key=lambda par: (_prioridad(par[1]), par[0]))
        for indice, llamada in orden:
            if not isinstance(llamada, dict) or indice in hechos:
                continue
            nombre = _nombre_llamada(llamada)
            resultado = ejecutar_herramienta(nombre, _argumentos_llamada(llamada))
            usadas.append(nombre)
            if isinstance(resultado.get("pendiente"), dict):
                pendientes.append(resultado["pendiente"])
            if resultado.get("texto"):
                textos.append(resultado["texto"])
            if isinstance(resultado.get("datos"), dict):
                datos.update(resultado["datos"])
            hechos[indice] = resultado
        for indice, llamada in enumerate(llamadas):
            if not isinstance(llamada, dict):
                continue
            resultado = hechos.get(indice) or {"ok": False, "texto": "No pude ejecutar esa herramienta."}
            mensajes.append(
                {
                    "role": "tool",
                    "tool_call_id": llamada.get("id") or f"llamada-{indice}",
                    "content": _resultado_tool(resultado),
                }
            )
    return _empaquetar_uso(
        "\n\n".join(textos) or "Necesito una frase un poco más concreta para seguir.",
        pendientes,
        usadas,
        datos,
        partes_uso,
    )


def _modelo_no_disponible() -> dict:
    return solo_texto("El modelo no está disponible.", "modelo")


def _sumar_aprendizaje(resultado: dict, texto: str) -> str:
    frase, id_mem = memoria.aprender(texto)
    if not frase:
        return ""
    aviso = f"Aprendí: {frase}"
    respuesta = (resultado.get("respuesta") or "").rstrip()
    if aviso not in respuesta:
        resultado["respuesta"] = f"{respuesta}\n\n{aviso}" if respuesta else aviso
    return id_mem


def _resultado_detenido(texto: str = "Detenido.") -> dict:
    return {
        "ok": True,
        "respuesta": texto,
        "pendientes_nuevos": [],
        "herramientas": [],
        "modo": "detenido",
        "datos": {},
        "detenido": True,
        "uso": _uso_publico({}),
    }


def chat(mensaje: str, chat_id: str | None = None, al_evento=None, nota: str = "") -> dict:
    from back import aprobacion

    texto = (mensaje or "").strip()
    if not texto:
        return solo_texto("Escribe lo que necesitas.")
    if chat_id:
        chats.activar(chat_id)
    cancelar = empezar_generacion()
    inicio = time.perf_counter()
    token = _turno.set(texto)
    try:
        try:
            resultado = chat_modelo(texto, cancelar=cancelar, al_evento=al_evento)
        except GeneracionDetenida:
            resultado = _resultado_detenido()
        except Exception as exc:
            if _cancelado(cancelar):
                resultado = _resultado_detenido()
            else:
                frase = _frase_del_fallo(exc)
                from back.registro import registrar

                registrar("modelo", frase)
                resultado = solo_texto(frase, "modelo")
        memoria_id = "" if resultado.get("detenido") else _sumar_aprendizaje(resultado, texto)
    finally:
        _turno.reset(token)
    if nota and nota not in (resultado.get("respuesta") or ""):
        respuesta = (resultado.get("respuesta") or "").rstrip()
        resultado["respuesta"] = f"{respuesta}\n\n{nota}" if respuesta else nota
    segundos = round(time.perf_counter() - inicio, 2)
    uso = dict(resultado.get("uso") or _uso_publico({}))
    uso["segundos"] = segundos
    resultado["uso"] = uso
    ids = []
    rutas_pendientes = []
    for pendiente in resultado.get("pendientes_nuevos") or []:
        if isinstance(pendiente, dict):
            if pendiente.get("id"):
                ids.append(pendiente["id"])
            rutas_pendientes.append(f"{pendiente.get('titulo')}: {pendiente.get('resumen')}")
    extra = (
        "Nota interna para el siguiente turno, no la repitas: acciones que sí quedaron pendientes: "
        + " | ".join(rutas_pendientes)
        if rutas_pendientes
        else ""
    )
    guardado = chats.anexar_turno(
        texto,
        resultado.get("respuesta") or "",
        ids,
        uso,
        memoria_id,
        extra,
        detenido=bool(resultado.get("detenido")),
    )
    if memoria_id:
        memoria.marcar_turno(memoria_id, guardado["mensaje_usuario"])
    if ids:
        try:
            aprobacion.etiquetar(ids, guardado["chat_id"], guardado["mensaje_usuario"])
        except Exception:
            pass
    resultado["uso_sesion"] = guardado.get("uso") or chats.uso_activo()
    resultado["chat_id"] = guardado.get("chat_id")
    resultado["mensaje_usuario"] = guardado.get("mensaje_usuario")
    resultado["modelo"] = _nombre_modelo()
    _guardar_ultimo(uso, bool(resultado.get("detenido")))
    gpu = ultimo_turno()
    resultado["en_gpu"] = gpu.get("en_gpu")
    resultado["gpu"] = gpu.get("gpu")
    return resultado


def editar(mensaje_id: str, texto: str, chat_id: str | None = None, al_evento=None) -> dict:
    from back import aprobacion

    info = chats.retroceder(chat_id, mensaje_id)
    decision = aprobacion.retirar(info.get("pendientes") or [])
    memoria.quitar_relacionados(info.get("memoria_ids") or [], info.get("turnos") or [])
    nota = ""
    if decision.get("ausentes"):
        nota = "Si alguna acción de esos mensajes ya se había aprobado, el archivo en disco sigue donde está."
    return chat(texto, chat_id=info.get("chat_id"), al_evento=al_evento, nota=nota)
