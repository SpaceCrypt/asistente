"""Memoria local del asistente. Vive en datos/ y no pasa por la cola de aprobación."""

from __future__ import annotations

import re
import threading
import unicodedata
import uuid

from back.apoyo import ahora, guardar_json, leer_json
from back.config import DATOS

_ARCHIVO = DATOS / "memoria.json"
_candado = threading.Lock()
_MAX = 80
_TOPE = 220
_STOP = {
    "el", "la", "los", "las", "de", "del", "al", "un", "una", "unos", "unas",
    "y", "o", "u", "a", "en", "por", "para", "con", "sin", "que", "te", "me",
    "se", "lo", "le", "les", "su", "sus", "es", "son", "como", "cuando",
    "siempre", "nunca", "nombre", "usa", "usar", "uses", "pida", "pidas",
    "archivo", "archivos",
}
_SECRETO = re.compile(
    r"(?i)\b(contraseñas?|passwords?|tokens?|api[-_ ]?keys?|secretos?|bearer|sk-[a-z0-9]{8,})\b"
    r"|(?:^|\s)(?:ASISTENTE_API_KEY|PASSWORD|SECRET|TOKEN)\s*=",
)
_NOMBRE = re.compile(
    r"(?i)cuando\s+(?:te\s+)?pida[sn]?\s+(.+?)\s*,?\s*usa(?:r)?\s+siempre\s+"
    r"(?:el\s+nombre\s+|el\s+archivo\s+)?[\"“«']?([^\"”»'\n.,]+)"
)
_PROHIBICION = re.compile(
    r"(?i)\bno\s+(?:me\s+)?(?:creas|crees|hagas|guardes|escribas|pongas|dejes|toques)\b.+"
)
_GUSTO = re.compile(
    r"(?i)\b(?:prefiero(?:\s+que)?|me gusta que|a partir de ahora|recuerda que)\b\s*.+"
)
_SIEMPRE = re.compile(r"(?i)\b(?:siempre|nunca)\b")
_TEMA_SIEMPRE = re.compile(r"(?i)\b(?:nombre|llame|llamar|carpeta|nota|escritorio|uses|usas)\b")


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(caracter for caracter in descompuesto if unicodedata.category(caracter) != "Mn").strip()


def _raiz(palabra: str) -> str:
    if len(palabra) > 4 and palabra.endswith("es"):
        return palabra[:-2]
    if len(palabra) > 3 and palabra.endswith("s"):
        return palabra[:-1]
    return palabra


def _palabras(texto: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9áéíóúñü-]{3,}", (texto or "").lower())
        if token not in _STOP
    }


def _recortar(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip())[:_TOPE].strip(" .")


def _es_secreto(texto: str) -> bool:
    return bool(_SECRETO.search(texto or ""))


def _cargar() -> list[dict]:
    datos = leer_json(_ARCHIVO, {"items": []})
    items = datos.get("items") if isinstance(datos, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _volcar(items: list[dict]) -> None:
    guardar_json(_ARCHIVO, {"items": items[-_MAX:]})


def _existe(items: list[dict], texto: str) -> bool:
    clave = _normalizar(texto)
    return any(_normalizar(str(item.get("texto") or "")) == clave for item in items)


def _puntua(item: dict, palabras: set[str]) -> int:
    claves = item.get("claves") if isinstance(item.get("claves"), list) else []
    base = {str(clave).lower() for clave in claves} or _palabras(str(item.get("texto") or ""))
    raices = {_raiz(palabra) for palabra in palabras}
    return len(raices & {_raiz(clave) for clave in base})


def _agregar(item: dict) -> str:
    with _candado:
        items = _cargar()
        if _existe(items, item["texto"]):
            return ""
        item["id"] = uuid.uuid4().hex
        item["creado"] = ahora()
        items.append(item)
        _volcar(items)
        return item["id"]


def es_solo_preferencia(mensaje: str) -> bool:
    texto = (mensaje or "").strip()
    if len(texto) < 12 or len(texto) > 400 or _es_secreto(texto):
        return False
    normal = _normalizar(texto)
    if re.search(r"\b(crea|crear|haz|hacer|escribe|escribir|guarda|guardar|borra|borrar|elimina|eliminar|mueve|mover|envia|enviar)\b", normal):
        return False
    return bool(_NOMBRE.search(texto) or _PROHIBICION.search(texto) or _GUSTO.search(texto) or (_SIEMPRE.search(texto) and _TEMA_SIEMPRE.search(texto)))


def aprender(mensaje: str) -> tuple[str, str]:
    """Guarda una preferencia nueva. Devuelve la frase y su id, o dos cadenas vacías."""
    texto = (mensaje or "").strip()
    if len(texto) < 12 or len(texto) > 800 or _es_secreto(texto):
        return "", ""
    nombre = _NOMBRE.search(texto)
    if nombre:
        tema = _recortar(nombre.group(1))
        archivo = _recortar(nombre.group(2)).strip("\"'“”«»")
        if not tema or not archivo or _es_secreto(archivo):
            return "", ""
        frase = f"Cuando pida {tema}, el archivo se llama {archivo}."
        item = {
            "tipo": "nombre",
            "texto": frase,
            "cuando": tema,
            "nombre": archivo,
            "claves": sorted(_palabras(f"{tema} {archivo}") | _palabras(tema)),
        }
        id_mem = _agregar(item)
        return (frase, id_mem) if id_mem else ("", "")
    prohibicion = _PROHIBICION.search(texto)
    if prohibicion:
        frase = _recortar(prohibicion.group(0))
        if _es_secreto(frase):
            return "", ""
        item = {"tipo": "correccion", "texto": frase, "claves": sorted(_palabras(frase))}
        id_mem = _agregar(item)
        return (frase, id_mem) if id_mem else ("", "")
    gusto = _GUSTO.search(texto)
    if gusto and len(texto) <= 400:
        frase = _recortar(gusto.group(0))
        if _es_secreto(frase):
            return "", ""
        item = {"tipo": "preferencia", "texto": frase, "claves": sorted(_palabras(frase))}
        id_mem = _agregar(item)
        return (frase, id_mem) if id_mem else ("", "")
    if len(texto) <= 320 and _SIEMPRE.search(texto) and _TEMA_SIEMPRE.search(texto):
        frase = _recortar(texto)
        if _es_secreto(frase):
            return "", ""
        item = {"tipo": "preferencia", "texto": frase, "claves": sorted(_palabras(frase))}
        id_mem = _agregar(item)
        return (frase, id_mem) if id_mem else ("", "")
    return "", ""


def olvidar() -> int:
    with _candado:
        cantidad = len(_cargar())
        _volcar([])
        return cantidad


def marcar_turno(id_memoria: str, turno: str) -> None:
    if not id_memoria or not turno:
        return
    with _candado:
        items = _cargar()
        for item in items:
            if item.get("id") == id_memoria:
                item["turno"] = turno
                _volcar(items)
                return


def quitar_relacionados(ids: list[str], turnos: list[str] | None = None) -> int:
    """Quita hechos aprendidos solo en los turnos que el usuario acaba de borrar."""
    ids_set = {valor for valor in ids or [] if valor}
    turnos_set = {valor for valor in (turnos or []) if valor}
    if not ids_set and not turnos_set:
        return 0
    with _candado:
        items = _cargar()
        quedaron = []
        borrados = 0
        for item in items:
            asociado = item.get("id") in ids_set or (item.get("turno") and item.get("turno") in turnos_set)
            if asociado:
                borrados += 1
                continue
            quedaron.append(item)
        if borrados:
            _volcar(quedaron)
        return borrados


def anotar_decision(aprobada: bool, titulo: str, resumen: str) -> None:
    titulo_limpio = _recortar(titulo or "una acción")
    resumen_limpio = _recortar((resumen or "").splitlines()[0] if resumen else "")
    if _es_secreto(titulo_limpio) or _es_secreto(resumen_limpio):
        return
    verbo = "aprobó" if aprobada else "rechazó"
    detalle = f" ({resumen_limpio})" if resumen_limpio else ""
    frase = _recortar(f"El usuario {verbo} «{titulo_limpio}»{detalle}. No repitas esa misma ruta.")
    _agregar(
        {
            "tipo": "aprobacion" if aprobada else "rechazo",
            "texto": frase,
            "claves": sorted(_palabras(f"{titulo_limpio} {resumen_limpio}")),
        }
    )


def relevantes(mensaje: str, limite: int = 8) -> list[dict]:
    palabras = _palabras(mensaje)
    with _candado:
        items = _cargar()
    puntuados = [( _puntua(item, palabras), item) for item in items]
    elegidos = [item for puntos, item in puntuados if puntos > 0]
    elegidos.sort(key=lambda item: item.get("creado") or "", reverse=True)
    return elegidos[:limite]


def bloque(mensaje: str) -> str:
    elegidos = relevantes(mensaje)
    if not elegidos:
        return ""
    lineas = "\n".join(f"- {item.get('texto')}" for item in elegidos)
    nombres = [item for item in elegidos if item.get("tipo") == "nombre" and item.get("nombre")]
    pista = ""
    if nombres:
        nombre = str(nombres[0].get("nombre"))
        pista = (
            f" Si piden crear eso, el archivo se llama {nombre}. "
            "La carpeta la decide la persona o la búsqueda, no un sitio fijo."
        )
    return (
        "Memoria local de este PC. No la inventes ni la contradigas. No sale del equipo.\n"
        f"{lineas}\n"
        "Si una memoria fija un nombre de archivo, úsalo tal cual en la ruta. "
        "No repitas una acción ya rechazada si es la misma ruta. Otras rutas sí puedes proponerlas."
        f"{pista}"
    )


def regla_de_nombre(mensaje: str) -> dict | None:
    mejor: tuple[int, dict] | None = None
    palabras = _palabras(mensaje)
    with _candado:
        items = [item for item in _cargar() if item.get("tipo") == "nombre" and item.get("nombre")]
    for item in items:
        puntos = _puntua(item, palabras)
        if puntos > 0 and (mejor is None or puntos > mejor[0]):
            mejor = (puntos, item)
    return mejor[1] if mejor else None

