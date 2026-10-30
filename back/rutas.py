"""Acceso a la PC del usuario: su perfil y los discos locales. Las credenciales quedan fuera."""

from __future__ import annotations

import os
import re
from pathlib import Path

from back.config import PROYECTO

NOMBRES_SECRETOS = {".env", "credentials.json", "client_secret.json", "token.json"}
NOMBRES_CLAVE_NAVEGADOR = {
    "login data",
    "login data for account",
    "logins.json",
    "key4.db",
    "key3.db",
}
PARTES_INTERNAS = {"venv", ".git", "__pycache__"}
NOMBRES_RESERVADOS = {
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
}


class PermisoRuta(Exception):
    """La ruta sale de las carpetas permitidas o apunta a un secreto."""


def perfil_usuario() -> Path:
    valor = os.environ.get("USERPROFILE", "").strip()
    if not valor:
        raise PermisoRuta("No encuentro la carpeta del usuario (USERPROFILE).")
    return Path(valor)


def _fisica(ruta: Path) -> Path:
    try:
        try:
            return ruta.resolve(strict=False)
        except TypeError:
            return ruta.resolve()
    except OSError as exc:
        raise PermisoRuta("No se pudo resolver la ruta.") from exc


def discos_locales() -> list[tuple[str, Path]]:
    """Discos fijos de esta máquina. No incluye redes ni unidades que no estén aquí."""
    import ctypes

    kernel = ctypes.windll.kernel32
    mascara = kernel.GetLogicalDrives()
    salida = []
    for indice in range(26):
        if not mascara & (1 << indice):
            continue
        letra = chr(ord("A") + indice)
        raiz = f"{letra}:\\"
        if kernel.GetDriveTypeW(raiz) != 3:
            continue
        salida.append((f"Disco {letra}", Path(raiz)))
    return salida


def raices() -> list[tuple[str, Path]]:
    pares = [("Perfil", perfil_usuario()), *discos_locales()]
    return [(nombre, _fisica(ruta)) for nombre, ruta in pares]


def _mapa_alias() -> dict[str, Path]:
    base = perfil_usuario()
    escritorio = _fisica(base / "Desktop")
    documentos = _fisica(base / "Documents")
    descargas = _fisica(base / "Downloads")
    proyecto = _fisica(PROYECTO)
    perfil = _fisica(base)
    mapa = {
        "escritorio": escritorio,
        "desktop": escritorio,
        "documentos": documentos,
        "documents": documentos,
        "descargas": descargas,
        "downloads": descargas,
        "proyecto": proyecto,
        "project": proyecto,
        "perfil": perfil,
        "usuario": perfil,
    }
    for etiqueta, ruta in discos_locales():
        letra = etiqueta.rsplit(" ", 1)[-1].lower()
        mapa[f"disco {letra}"] = _fisica(ruta)
    return mapa


def esta_dentro(candidato: Path, raiz: Path) -> bool:
    actual = os.path.normcase(str(candidato))
    base = os.path.normcase(str(raiz))
    if actual == base:
        return True
    if not base.endswith(os.sep):
        base += os.sep
    return actual.startswith(base)


def nombre_raiz(ruta: Path) -> str:
    mejor = "Carpeta"
    mejor_largo = -1
    for nombre, raiz in raices():
        if esta_dentro(ruta, raiz) and len(str(raiz)) > mejor_largo:
            mejor = nombre
            mejor_largo = len(str(raiz))
    return mejor


def _rechazar_texto(texto: str) -> None:
    if "\x00" in texto or any(caracter in texto for caracter in '*?<>|'):
        raise PermisoRuta("La ruta tiene caracteres no permitidos.")
    revisable = texto[2:] if re.match(r"^[A-Za-z]:", texto) else texto
    if ":" in revisable:
        raise PermisoRuta("La ruta no es válida.")


def _normalizar_alias(texto: str) -> str:
    import unicodedata

    descompuesto = unicodedata.normalize("NFD", texto)
    sin_acentos = "".join(
        caracter for caracter in descompuesto if unicodedata.category(caracter) != "Mn"
    )
    return sin_acentos.lower().replace("\\", "/").strip().rstrip("/")


def _empieza_por_alias(texto: str) -> bool:
    normal = _normalizar_alias(texto)
    return any(normal == alias or normal.startswith(alias + "/") for alias in _mapa_alias())


def _quitar_articulo(texto: str) -> str:
    coincidencia = re.match(r"^(?:mi|el|la|mis|los|las)\s+(.+)$", texto, flags=re.IGNORECASE)
    if not coincidencia:
        return texto
    resto = coincidencia.group(1).strip()
    if _empieza_por_alias(resto):
        return resto
    return texto


def _rechazar_interno(ruta: Path) -> None:
    proyecto = _fisica(PROYECTO)
    if not esta_dentro(ruta, proyecto):
        return
    relativo = os.path.normcase(str(ruta))[len(os.path.normcase(str(proyecto))):]
    partes = {parte for parte in re.split(r"[\\/]", relativo) if parte}
    if partes & PARTES_INTERNAS:
        raise PermisoRuta("Esa ruta es parte del programa (venv o git) y no la toco.")


def _es_clave_navegador(ruta: Path) -> bool:
    return any(parte.casefold() in NOMBRES_CLAVE_NAVEGADOR for parte in ruta.parts)


def rechazar_secreto(ruta: Path) -> None:
    if ruta.name.lower() in NOMBRES_SECRETOS or _es_clave_navegador(ruta):
        raise PermisoRuta(
            "No leo ni muestro .env, credentials.json, token.json ni las contraseñas del navegador."
        )


def _sin_comillas(texto: str) -> str:
    limpio = (texto or "").strip()
    for marca in "\"'«»“”":
        limpio = limpio.replace(marca, "")
    return " ".join(limpio.split())


def resolver_ruta(entrada: str | None, por_defecto: str = "escritorio") -> Path:
    texto = _sin_comillas(entrada if entrada is not None else por_defecto) or por_defecto
    texto = texto.replace(" / ", "/")
    if re.fullmatch(r"[A-Za-z]:", texto):
        texto += "\\"
    _rechazar_texto(texto)
    texto = os.path.expanduser(os.path.expandvars(texto))
    _rechazar_texto(texto)
    candidato = Path(texto)
    if not candidato.is_absolute():
        texto = _quitar_articulo(texto)
        normal = _normalizar_alias(texto)
        alias = sorted(_mapa_alias().items(), key=lambda par: len(par[0]), reverse=True)
        for nombre, raiz in alias:
            if normal == nombre:
                return _exigir(raiz)
            if normal.startswith(nombre + "/"):
                resto = texto[len(nombre) :].lstrip("\\/")
                return _exigir(raiz / resto)
        candidato = PROYECTO / texto
    return _exigir(candidato)


def _rechazar_reservado(ruta: Path) -> None:
    nombre = ruta.name.lower().split(".", 1)[0]
    if nombre in NOMBRES_RESERVADOS:
        raise PermisoRuta("Ese nombre de archivo no está permitido en Windows.")


def _exigir(candidato: Path) -> Path:
    resuelto = _fisica(candidato)
    texto = str(resuelto)
    if texto.startswith("\\\\.\\") or texto.startswith("\\\\?\\"):
        raise PermisoRuta("La ruta no es válida.")
    if not any(esta_dentro(resuelto, raiz) for _, raiz in raices()):
        raise PermisoRuta("Esa ruta no está en los discos de este equipo.")
    _rechazar_interno(resuelto)
    rechazar_secreto(resuelto)
    _rechazar_reservado(resuelto)
    return resuelto


def raices_publicas() -> list[dict]:
    try:
        perfil_ok = perfil_usuario().is_dir()
    except PermisoRuta:
        perfil_ok = False
    discos_ok = any(ruta.is_dir() for _, ruta in discos_locales())
    return [{"nombre": "Este equipo", "existe": perfil_ok or discos_ok}]


def describir(ruta: Path) -> str:
    """Nombre de la carpeta permitida y el resto, sin la unidad del disco."""
    try:
        resuelta = _fisica(Path(ruta))
    except OSError:
        resuelta = Path(ruta)
    for nombre, raiz in raices():
        if not esta_dentro(resuelta, raiz):
            continue
        try:
            resto = resuelta.relative_to(raiz)
        except ValueError:
            return nombre
        if not resto.parts:
            return nombre
        return " / ".join((nombre, *resto.parts))
    return Path(ruta).name
