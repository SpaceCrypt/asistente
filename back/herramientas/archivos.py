"""Archivos: leer y listar al momento; escribir, borrar y mover solo tras aprobar."""

from __future__ import annotations

import ast
import difflib
import json
import os
import re
import uuid
import zipfile
from pathlib import Path

from back import aprobacion
from back.apoyo import capturar
from back.registro import registrar
from back import rutas

LIMITE_LISTA = 200
LIMITE_LECTURA = 100_000
LIMITE_ESCRITURA = 500_000


def _tamano(valor: int) -> str:
    if valor < 1024:
        return f"{valor} B"
    if valor < 1024 * 1024:
        return f"{valor / 1024:.1f} KB"
    return f"{valor / (1024 * 1024):.1f} MB"


def _leer_bytes(ruta: Path) -> tuple[str, bool]:
    datos = ruta.read_bytes()
    if b"\x00" in datos[:4096]:
        raise ValueError("Ese archivo no es texto y no lo abro.")
    recortado = len(datos) > LIMITE_LECTURA
    fragmento = datos[:LIMITE_LECTURA]
    return fragmento.decode("utf-8", errors="replace"), recortado


def _diff(ruta: Path, contenido: str) -> str:
    if ruta.exists() and ruta.is_file():
        anterior, recortado = _leer_bytes(ruta)
        if recortado:
            anterior += "\n… (lectura recortada)"
        lineas = difflib.unified_diff(
            anterior.splitlines(),
            contenido.splitlines(),
            fromfile="actual",
            tofile="propuesto",
            lineterm="",
        )
        return "\n".join(lineas) or "El texto no cambia."
    nuevas = contenido.splitlines() or ["(vacío)"]
    return "Archivo nuevo:\n" + "\n".join("+ " + linea for linea in nuevas)


@capturar
def listar(directorio: str | None = None) -> dict:
    pedido = directorio or "escritorio"
    ruta = rutas.resolver_ruta(pedido)
    nota = ""
    if not ruta.exists() or not ruta.is_dir():
        hallazgo = _ubicar(pedido, "carpeta")
        if isinstance(hallazgo, list):
            return _respuesta_varias(hallazgo)
        if not isinstance(hallazgo, Path) or not hallazgo.is_dir():
            raise FileNotFoundError(
                "No está en la ruta que armaste y no encontré esa carpeta en el Escritorio ni en el resto del equipo."
            )
        nota = f"No estaba en la ruta que armaste. La carpeta está en {_referencia(hallazgo)}.\n"
        ruta = hallazgo
    if not ruta.is_dir():
        raise ValueError("Esa ruta no es una carpeta. Si quieres ver un archivo, pídeme que lo lea.")
    entradas = []
    omitidos = 0
    try:
        hijos = list(ruta.iterdir())
    except OSError as exc:
        raise OSError(f"No pude abrir la carpeta: {exc}") from exc
    for hijo in hijos:
        try:
            es_directorio = hijo.is_dir()
            tamano = None if es_directorio else hijo.stat().st_size
        except OSError:
            omitidos += 1
            continue
        if hijo.name.lower() in rutas.NOMBRES_SECRETOS or rutas._es_clave_navegador(hijo):
            continue
        entradas.append({"nombre": hijo.name, "es_directorio": es_directorio, "tamano": tamano})
    entradas.sort(key=lambda item: (not item["es_directorio"], item["nombre"].lower()))
    total = len(entradas)
    mostrados = entradas[:LIMITE_LISTA]
    nombre = rutas.describir(ruta)
    lineas = [nota + nombre, f"{total} elementos."]
    for item in mostrados:
        if item["es_directorio"]:
            lineas.append(f"[carpeta] {item['nombre']}")
        else:
            lineas.append(f"[archivo] {item['nombre']}  {_tamano(item['tamano'] or 0)}")
    if total > len(mostrados):
        lineas.append(f"Mostrando {len(mostrados)} de {total}.")
    if omitidos:
        lineas.append(f"No pude leer {omitidos} elementos.")
    registrar("lectura", f"Listó {nombre}, {total} elementos")
    return {
        "ok": True,
        "texto": "\n".join(lineas),
        "datos": {
            "ruta": str(ruta),
            "total": total,
            "entradas": [item["nombre"] for item in mostrados],
        },
    }


_OMITIR_CARPETAS = {
    "venv",
    ".git",
    "__pycache__",
    "node_modules",
    "system volume information",
    "$recycle.bin",
    "winsxs",
}
_TOPE_COINCIDENCIAS = 40
_TOPE_RECORRIDOS = 8000


def _alias_raiz(etiqueta: str) -> str:
    if etiqueta.casefold().startswith("disco "):
        return etiqueta.casefold()
    return etiqueta.casefold()


def _prohibido(ruta: Path) -> bool:
    return ruta.name.lower() in rutas.NOMBRES_SECRETOS or rutas._es_clave_navegador(ruta)


def _parecido(pedido: str, real: str) -> bool:
    """Mismo nombre, sin distinguir mayúsculas ni un plural sencillo. No trae una lista de carpetas."""

    def raiz(texto: str) -> str:
        palabras = []
        for palabra in texto.casefold().split():
            if len(palabra) > 3 and palabra.endswith("s") and not palabra.endswith("ss"):
                palabra = palabra[:-1]
            palabras.append(palabra)
        return " ".join(palabras)

    def palabra(izquierda: str, derecha: str) -> bool:
        if izquierda == derecha:
            return True
        una, otra = raiz(izquierda), raiz(derecha)
        if una == otra:
            return True
        corta, larga = (una, otra) if len(una) <= len(otra) else (otra, una)
        return len(corta) >= 4 and corta in larga

    pedidas = (pedido or "").casefold().split()
    reales = (real or "").casefold().split()
    if not pedidas or not reales:
        return False
    if len(pedidas) == len(reales) and all(palabra(a, b) for a, b in zip(pedidas, reales)):
        return True
    if len(pedidas) == 1 and any(palabra(pedidas[0], item) for item in reales):
        return True
    return False


def _partes_pedidas(entrada: str) -> list[str]:
    """Trozos de la ruta que pasó quien llama, sin la unidad ni las carpetas del sistema."""
    texto = (entrada or "").strip().strip("\"'«»“”")
    texto = texto.replace("\\", "/").replace(" / ", "/")
    texto = re.sub(r"^[A-Za-z]:", "", texto).lstrip("/")
    partes = [parte.strip() for parte in texto.split("/") if parte.strip() and parte.strip() not in {".", ".."}]
    alias = {nombre.casefold() for nombre in rutas._mapa_alias()}
    perfil = rutas.perfil_usuario()
    sistema = {
        "users",
        "desktop",
        "documents",
        "downloads",
        perfil.name.casefold(),
        PROYECTO_NOMBRE,
    }
    while partes and (partes[0].casefold() in alias or partes[0].casefold() in sistema):
        partes.pop(0)
    return partes


PROYECTO_NOMBRE = rutas.PROYECTO.name.casefold()


def _es_archivo(nombre: str) -> bool:
    return bool(Path(nombre).suffix)


def _referencia(ruta: Path) -> str:
    fisico = rutas._fisica(ruta)
    orden = sorted(rutas._mapa_alias().items(), key=lambda par: len(str(par[1])), reverse=True)
    for nombre, raiz in orden:
        if not rutas.esta_dentro(fisico, raiz):
            continue
        try:
            resto = fisico.relative_to(raiz)
        except ValueError:
            return nombre
        if not resto.parts:
            return nombre
        return nombre + "/" + "/".join(resto.parts)
    return rutas.describir(fisico)


def _respuesta_varias(halladas: list[Path]) -> dict:
    lineas = [f"Hay {len(halladas)} coincidencias. Pregunta cuál quiere."]
    datos = []
    for ruta in halladas:
        ref = _referencia(ruta)
        tipo = "carpeta" if ruta.is_dir() else "archivo"
        lineas.append(f"{tipo}: {ref}")
        datos.append({"nombre": ruta.name, "es_directorio": ruta.is_dir(), "referencia": ref})
    return {"ok": True, "texto": "\n".join(lineas), "datos": {"coincidencias": datos}}


def _respuesta_una(ruta: Path) -> dict:
    ref = _referencia(ruta)
    tipo = "la carpeta" if ruta.is_dir() else "el archivo"
    registrar("lectura", "Buscó por nombre y encontró 1")
    return {
        "ok": True,
        "texto": f"Encontré {tipo} {ref}.",
        "datos": {"coincidencias": [{"nombre": ruta.name, "es_directorio": ruta.is_dir(), "referencia": ref}]},
    }


def _zonas() -> list[tuple[Path, Path | None]]:
    escritorio = rutas._fisica(rutas._mapa_alias()["escritorio"])
    perfil = rutas._fisica(rutas.perfil_usuario())
    zonas = [(escritorio, None), (perfil, escritorio)]
    for _nombre, disco in rutas.discos_locales():
        fisico = rutas._fisica(disco)
        zonas.append((fisico, perfil if rutas.esta_dentro(perfil, fisico) else None))
    return zonas


def _hijos_dir(carpeta: Path) -> list[Path]:
    try:
        return [
            hijo
            for hijo in carpeta.iterdir()
            if hijo.is_dir() and hijo.name.casefold() not in _OMITIR_CARPETAS and not hijo.name.startswith(".")
        ]
    except OSError:
        return []


def _bajar(base: Path, carpetas: list[str]) -> list[Path]:
    actuales = [base]
    for nombre in carpetas:
        siguientes = []
        for actual in actuales:
            for hijo in _hijos_dir(actual):
                if _parecido(nombre, hijo.name):
                    siguientes.append(hijo)
        actuales = siguientes
        if not actuales:
            return []
    return actuales


def _walk_dirs(raiz: Path, nombre: str, excluir: Path | None) -> list[Path]:
    hallados = []
    recorridos = 0
    if not raiz.is_dir():
        return hallados
    for actual, directorios, _archivos in os.walk(raiz, onerror=lambda _exc: None):
        actual_path = Path(actual)
        if not rutas.esta_dentro(actual_path, raiz):
            directorios.clear()
            continue
        if excluir is not None and rutas.esta_dentro(actual_path, excluir):
            directorios.clear()
            continue
        directorios[:] = [
            item
            for item in directorios
            if item.casefold() not in _OMITIR_CARPETAS
            and not item.startswith(".")
            and not (excluir is not None and rutas.esta_dentro(actual_path / item, excluir))
        ]
        recorridos += 1
        if recorridos > _TOPE_RECORRIDOS:
            break
        for item in directorios:
            if _parecido(nombre, item):
                hallados.append(actual_path / item)
        if len(hallados) >= 8:
            break
    return hallados


def _carpetas_en(raiz: Path, carpetas: list[str], excluir: Path | None) -> list[Path]:
    if not carpetas or not raiz.is_dir():
        return []
    if excluir is not None and rutas.esta_dentro(raiz, excluir):
        return []
    directas = _bajar(raiz, carpetas)
    if directas:
        return directas
    anclas = _walk_dirs(raiz, carpetas[0], excluir)
    if len(carpetas) == 1:
        return anclas
    salida = []
    for ancla in anclas:
        salida.extend(_bajar(ancla, carpetas[1:]))
    return salida


def _txt_en(carpeta: Path, nombre: str | None, quiere_txt: bool) -> list[Path]:
    try:
        hijos = [hijo for hijo in carpeta.iterdir() if hijo.is_file() and not _prohibido(hijo)]
    except OSError:
        return []
    if nombre:
        exactos = [hijo for hijo in hijos if _parecido(nombre, hijo.name)]
        if exactos:
            return exactos
    if quiere_txt:
        return [hijo for hijo in hijos if hijo.suffix.lower() == ".txt"]
    return []


def _ubicar(entrada: str, modo: str) -> Path | list[Path] | None:
    """Si la ruta armada no existe, busca en el Escritorio y después en el resto del equipo.

    Los nombres salen de la ruta que pasó quien llama. No hay una lista fija de carpetas.
    """
    partes = _partes_pedidas(entrada)
    if not partes:
        return None
    nombre_archivo = None
    carpetas = list(partes)
    if _es_archivo(partes[-1]):
        nombre_archivo = partes[-1]
        carpetas = partes[:-1]
        if nombre_archivo.casefold() in rutas.NOMBRES_SECRETOS or nombre_archivo.casefold() in rutas.NOMBRES_CLAVE_NAVEGADOR:
            raise rutas.PermisoRuta("No leo ni muestro credenciales ni contraseñas del navegador.")
    if modo == "carpeta" and not carpetas:
        return None
    quiere_txt = modo == "txt" or (nombre_archivo or "").casefold().endswith(".txt")
    vistos: set[str] = set()
    for raiz, excluir in _zonas():
        if not raiz.is_dir():
            continue
        hallados: list[Path] = []
        if modo == "carpeta":
            candidatos = _carpetas_en(raiz, carpetas, excluir)
        elif not carpetas:
            continue
        else:
            candidatos = []
            for carpeta in _carpetas_en(raiz, carpetas, excluir):
                candidatos.extend(_txt_en(carpeta, nombre_archivo, quiere_txt))
        for candidato in candidatos:
            try:
                clave = os.path.normcase(str(rutas._fisica(candidato)))
            except rutas.PermisoRuta:
                continue
            if clave in vistos:
                continue
            vistos.add(clave)
            hallados.append(candidato)
        if len(hallados) == 1:
            return hallados[0]
        if hallados:
            return hallados
    return None


@capturar
def buscar(nombre: str) -> dict:
    """Busca por el texto que pasó quien llama. No trae una lista de nombres fija."""
    consulta = " ".join((nombre or "").replace("\\", "/").split()).strip("\"'«»“”")
    if not consulta:
        raise ValueError("Falta el nombre que hay que buscar.")
    if any(caracter in consulta for caracter in "*?<>|"):
        raise ValueError("Ese nombre no se puede buscar.")
    if len(consulta) > 180:
        raise ValueError("El nombre es demasiado largo.")
    if consulta.casefold() in rutas.NOMBRES_SECRETOS or consulta.casefold() in rutas.NOMBRES_CLAVE_NAVEGADOR:
        raise rutas.PermisoRuta("No leo ni muestro credenciales ni contraseñas del navegador.")
    consulta_cf = consulta.casefold()
    modo = "txt" if consulta_cf.endswith(".txt") else "carpeta"
    hallazgo = _ubicar(consulta, modo)
    if isinstance(hallazgo, Path):
        return _respuesta_una(hallazgo)
    if isinstance(hallazgo, list) and hallazgo:
        return _respuesta_varias(hallazgo)
    coincidencias = []
    recorridos = 0
    perfil = rutas._fisica(rutas.perfil_usuario())
    orden = sorted(rutas.raices(), key=lambda par: 0 if par[0] == "Perfil" else 1)
    for etiqueta, raiz in orden:
        if not raiz.is_dir() or len(coincidencias) >= _TOPE_COINCIDENCIAS:
            continue
        alias = _alias_raiz(etiqueta)
        for actual, directorios, archivos_nombres in os.walk(raiz, onerror=lambda _exc: None):
            actual_path = Path(actual)
            if not rutas.esta_dentro(actual_path, raiz):
                directorios.clear()
                continue
            if etiqueta != "Perfil" and rutas.esta_dentro(actual_path, perfil):
                directorios.clear()
                continue
            directorios[:] = [
                item for item in directorios if item.casefold() not in _OMITIR_CARPETAS and not item.startswith(".")
            ]
            if actual_path == raiz and etiqueta != "Perfil":
                directorios.sort(key=lambda item: (item.casefold() != "users", item.casefold()))
            recorridos += 1
            entradas = [(True, item) for item in directorios] + [(False, item) for item in archivos_nombres]
            for es_directorio, item in entradas:
                ruta = actual_path / item
                if _prohibido(ruta) or not rutas.esta_dentro(ruta, raiz):
                    continue
                try:
                    relativo = ruta.relative_to(raiz)
                except ValueError:
                    continue
                referencia = alias if not relativo.parts else alias + "/" + "/".join(relativo.parts)
                if (
                    consulta_cf not in item.casefold()
                    and consulta_cf not in referencia.casefold()
                    and not _parecido(consulta, item)
                ):
                    continue
                coincidencias.append(
                    {
                        "nombre": item,
                        "es_directorio": es_directorio,
                        "referencia": referencia,
                        "lugar": etiqueta,
                    }
                )
                if len(coincidencias) >= _TOPE_COINCIDENCIAS:
                    break
            if len(coincidencias) >= _TOPE_COINCIDENCIAS or recorridos >= _TOPE_RECORRIDOS:
                break
        if recorridos >= _TOPE_RECORRIDOS:
            break
    if not coincidencias:
        return {
            "ok": True,
            "texto": (
                "No hay ninguno con ese nombre en este equipo."
            ),
            "datos": {"coincidencias": []},
        }
    if len(coincidencias) == 1:
        item = coincidencias[0]
        tipo = "la carpeta" if item["es_directorio"] else "el archivo"
        texto = f"Encontré {tipo} {item['referencia']}."
    else:
        lineas = [f"Hay {len(coincidencias)} coincidencias. Pregunta cuál quiere."]
        for item in coincidencias:
            tipo = "carpeta" if item["es_directorio"] else "archivo"
            lineas.append(f"{tipo}: {item['referencia']}")
        texto = "\n".join(lineas)
    registrar("lectura", f"Buscó por nombre y encontró {len(coincidencias)}")
    return {"ok": True, "texto": texto, "datos": {"coincidencias": coincidencias}}


@capturar
def leer(ruta: str) -> dict:
    if not ruta or not str(ruta).strip():
        raise ValueError("Falta la ruta del archivo.")
    destino = rutas.resolver_ruta(ruta)
    nota = ""
    if not destino.exists() or not destino.is_file():
        modo = "txt" if str(ruta).casefold().endswith(".txt") else "archivo"
        hallazgo = _ubicar(ruta, modo)
        if isinstance(hallazgo, list):
            return _respuesta_varias(hallazgo)
        if not isinstance(hallazgo, Path) or not hallazgo.is_file():
            raise FileNotFoundError(
                "No está en la ruta que armaste y no encontré ese archivo en el Escritorio ni en el resto del equipo."
            )
        nota = f"No estaba en la ruta que armaste. El archivo está en {_referencia(hallazgo)}.\n\n"
        destino = hallazgo
    if destino.is_dir():
        raise ValueError("Eso es una carpeta. Pídeme que la liste.")
    if not destino.is_file():
        raise ValueError("Esa ruta no es un archivo.")
    texto, recortado = _leer_bytes(destino)
    if recortado:
        texto += "\n… (archivo recortado)"
    registrar("lectura", f"Leyó {rutas.describir(destino)}")
    return {
        "ok": True,
        "texto": f"{nota}{rutas.describir(destino)}\n\n{texto}",
        "datos": {"ruta": str(destino), "referencia": _referencia(destino)},
    }


@capturar
def proponer_escribir(ruta: str, contenido: str, motivo: str = "") -> dict:
    if not ruta or not str(ruta).strip():
        raise ValueError("Falta la ruta del archivo.")
    if not isinstance(contenido, str):
        raise ValueError("El contenido tiene que ser texto.")
    if len(contenido) > LIMITE_ESCRITURA:
        raise ValueError("El contenido es demasiado largo.")
    destino = rutas.resolver_ruta(ruta)
    if destino.suffix.lower() == ".docx":
        raise ValueError("Eso es un Word. Usa la preparación de documento .docx, no un archivo de texto.")
    if destino.suffix.lower() in {".xlsx", ".xls"}:
        raise ValueError("Eso es un Excel. Usa la preparación de .xlsx, no un archivo de texto.")
    if destino.suffix.lower() == ".pdf":
        raise ValueError("Eso es un PDF. Usa la preparación de PDF, no un archivo de texto.")
    if destino.exists() and not destino.is_file():
        raise ValueError("Esa ruta no es un archivo de texto.")
    if destino.exists():
        _leer_bytes(destino)
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    vista = _diff(destino, contenido)
    if len(vista) > 20000:
        vista = vista[:20000] + "\n… (diff recortado)"
    nuevo = not destino.exists()
    titulo = "Crear archivo" if nuevo else "Modificar archivo"
    motivo_limpio = (motivo or "Pedido en el chat").strip()
    vista = f"{destino}\nMotivo: {motivo_limpio}\n\n{vista}"
    pendiente = aprobacion.agregar(
        "escribir",
        titulo,
        str(destino),
        vista,
        {
            "ruta": str(destino),
            "contenido": contenido,
            "motivo": motivo_limpio,
            "nuevo": nuevo,
        },
    )
    texto = (
        f"Preparé {'un archivo nuevo' if nuevo else 'un cambio'} en {destino}. "
        "Aún no escribí nada. Revísalo en Acciones pendientes y pulsa Aprobar o Rechazar."
    )
    return {"ok": True, "texto": texto, "pendiente": pendiente}


@capturar
def proponer_borrar(ruta: str, motivo: str = "") -> dict:
    if not ruta or not str(ruta).strip():
        raise ValueError("Falta la ruta del archivo.")
    destino = rutas.resolver_ruta(ruta)
    if not destino.exists():
        raise FileNotFoundError(f"No existe ese archivo: {rutas.describir(destino)}")
    if not destino.is_file():
        raise ValueError("Solo puedo proponer borrar archivos, no carpetas.")
    motivo_limpio = (motivo or "Pedido en el chat").strip()
    vista = f"Se borrará este archivo:\n{destino}\n\nMotivo: {motivo_limpio}"
    pendiente = aprobacion.agregar(
        "borrar",
        "Borrar archivo",
        str(destino),
        vista,
        {"ruta": str(destino), "motivo": motivo_limpio},
    )
    return {
        "ok": True,
        "texto": f"Preparé el borrado de {destino}. Aún no borré nada. Pulsa Aprobar o Rechazar.",
        "pendiente": pendiente,
    }


def _buscar_archivo(entrada: str, modo: str):
    """Si la ruta armada no está, busca en el Escritorio por los trozos de esa ruta."""
    try:
        directo = rutas.resolver_ruta(entrada)
    except rutas.PermisoRuta:
        raise
    if directo.is_file():
        return directo
    return _ubicar(entrada, modo)


@capturar
def proponer_mover(origen: str, destino: str, motivo: str = "") -> dict:
    if not origen or not destino:
        raise ValueError("Faltan el origen y el destino.")
    modo = "txt" if str(origen).casefold().endswith(".txt") else "archivo"
    hallazgo = _buscar_archivo(origen, modo)
    if isinstance(hallazgo, list):
        return _respuesta_varias(hallazgo)
    if not isinstance(hallazgo, Path) or not hallazgo.is_file():
        raise FileNotFoundError(
            "No está en la ruta que armaste y no encontré ese archivo en el Escritorio ni en el resto del equipo."
        )
    ruta_origen = hallazgo
    if ruta_origen.suffix.lower() == ".txt" and str(destino).casefold().endswith((".xlsx", ".xls")):
        return proponer_crear_excel(str(ruta_origen), motivo=motivo or "Pasar el texto a Excel")
    ruta_destino = rutas.resolver_ruta(destino)
    if not ruta_origen.is_file():
        raise ValueError("Solo puedo mover archivos, no carpetas.")
    if ruta_destino.exists():
        raise ValueError("El destino ya existe. Elige otro nombre.")
    if ruta_origen == ruta_destino:
        raise ValueError("El origen y el destino son el mismo archivo.")
    padre = ruta_destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    motivo_limpio = (motivo or "Pedido en el chat").strip()
    vista = f"Mover:\n{ruta_origen}\nhacia:\n{ruta_destino}\n\nMotivo: {motivo_limpio}"
    pendiente = aprobacion.agregar(
        "mover",
        "Mover archivo",
        ruta_origen.name,
        vista,
        {"origen": str(ruta_origen), "destino": str(ruta_destino), "motivo": motivo_limpio},
    )
    return {
        "ok": True,
        "texto": f"Preparé el movimiento de {ruta_origen} a {ruta_destino}. Aún no moví nada. Pulsa Aprobar o Rechazar.",
        "pendiente": pendiente,
    }


@capturar
def proponer_crear_carpeta(ruta: str, motivo: str = "") -> dict:
    if not ruta or not str(ruta).strip():
        raise ValueError("Falta la ruta de la carpeta.")
    destino = rutas.resolver_ruta(ruta)
    if destino.exists():
        if destino.is_dir():
            raise ValueError(f"Esa carpeta ya existe: {destino}")
        raise ValueError(f"Ya hay un archivo con ese nombre: {destino}")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta donde iría esta no es válida.")
    motivo_limpio = (motivo or "Pedido en el chat").strip()
    vista = f"Se creará esta carpeta:\n{destino}\n\nMotivo: {motivo_limpio}\n\nAprueba esta acción antes que el archivo que vaya dentro."
    pendiente = aprobacion.agregar(
        "carpeta",
        "Crear carpeta",
        str(destino),
        vista,
        {"ruta": str(destino), "motivo": motivo_limpio},
    )
    return {
        "ok": True,
        "texto": f"Preparé la carpeta {destino}. Aún no la creé. Pulsa Aprobar o Rechazar.",
        "pendiente": pendiente,
    }


def _ruta_docx(ruta: str) -> str:
    limpia = (ruta or "").strip().strip('"').strip("'")
    if not limpia:
        raise ValueError("Falta la ruta del documento Word.")
    if not limpia.lower().endswith(".docx"):
        limpia += ".docx"
    return limpia


@capturar
def proponer_crear_word(ruta: str, texto: str, motivo: str = "") -> dict:
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("Falta el texto que irá dentro del Word.")
    if len(texto) > LIMITE_ESCRITURA:
        raise ValueError("El texto es demasiado largo.")
    destino = rutas.resolver_ruta(_ruta_docx(ruta))
    if destino.suffix.lower() != ".docx":
        raise ValueError("El documento tiene que terminar en .docx.")
    if destino.exists() and not destino.is_file():
        raise ValueError("Esa ruta no es un archivo.")
    if destino.exists():
        raise ValueError(f"Ese documento ya existe: {destino}")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    motivo_limpio = (motivo or "Pedido en el chat").strip()
    vista = (
        f"Se creará un documento Word (.docx), con el texto dentro:\n{destino}\n\n"
        f"Motivo: {motivo_limpio}\n\n"
        f"Texto dentro del documento:\n{texto}\n\n"
        "Si la carpeta también está pendiente, aprueba primero la carpeta y después este documento."
    )
    pendiente = aprobacion.agregar(
        "word",
        "Crear documento Word",
        str(destino),
        vista,
        {"ruta": str(destino), "texto": texto, "motivo": motivo_limpio},
    )
    return {
        "ok": True,
        "texto": (
            f"Preparé el documento Word {destino} con el texto dentro. "
            "Aún no lo creé. Si la carpeta también está pendiente, aprueba primero la carpeta."
        ),
        "pendiente": pendiente,
    }


def _escribir(accion: dict) -> str:
    detalle = accion["detalle"]
    destino = rutas.resolver_ruta(detalle["ruta"])
    contenido = detalle.get("contenido")
    if not isinstance(contenido, str):
        raise ValueError("El contenido guardado no es texto.")
    if len(contenido) > LIMITE_ESCRITURA:
        raise ValueError("El contenido es demasiado largo.")
    if destino.exists() and not destino.is_file():
        raise ValueError("La ruta ya no es un archivo.")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    padre.mkdir(parents=True, exist_ok=True)
    temporal = padre / f".{destino.name}.{uuid.uuid4().hex}.asistente.tmp"
    try:
        with temporal.open("w", encoding="utf-8", newline="\n") as archivo:
            archivo.write(contenido)
        temporal.replace(destino)
    finally:
        if temporal.exists():
            try:
                temporal.unlink()
            except OSError:
                pass
    return f"Escribí {destino} ({len(contenido)} caracteres)."


def _borrar(accion: dict) -> str:
    destino = rutas.resolver_ruta(accion["detalle"]["ruta"])
    if not destino.exists():
        raise FileNotFoundError(f"Ya no existe: {destino}")
    if not destino.is_file():
        raise ValueError("Solo borro archivos, no carpetas.")
    destino.unlink()
    return f"Borré {destino}."


def _crear_carpeta(accion: dict) -> str:
    destino = rutas.resolver_ruta(accion["detalle"]["ruta"])
    if destino.exists():
        if destino.is_dir():
            return f"La carpeta ya estaba creada: {destino}."
        raise ValueError("Ya hay un archivo con ese nombre.")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta donde iría esta no es válida.")
    destino.mkdir(parents=True, exist_ok=True)
    return f"Creé la carpeta {destino}."


def _parrafos(texto: str) -> list[str]:
    lineas = texto.splitlines()
    return lineas if lineas else [texto]


def _es_zip_con(ruta: Path, miembro: str) -> bool:
    if not zipfile.is_zipfile(ruta):
        return False
    with zipfile.ZipFile(ruta) as archivo:
        nombres = {nombre.replace("\\", "/") for nombre in archivo.namelist()}
    return miembro in nombres


def _exigir_docx(ruta: Path) -> None:
    if not _es_zip_con(ruta, "word/document.xml"):
        raise ValueError("El archivo generado no es un Word .docx válido.")


def _exigir_xlsx(ruta: Path) -> None:
    if not _es_zip_con(ruta, "xl/workbook.xml"):
        raise ValueError("El archivo generado no es un Excel .xlsx válido.")


def _exigir_pdf(ruta: Path) -> None:
    if not ruta.read_bytes().startswith(b"%PDF"):
        raise ValueError("El archivo generado no es un PDF válido.")


def _ruta_con_extension(ruta: str, extension: str, etiqueta: str) -> str:
    limpia = (ruta or "").strip().strip('"').strip("'")
    if not limpia:
        raise ValueError(f"Falta la ruta del {etiqueta}.")
    sufijo = Path(limpia).suffix.lower()
    if sufijo and sufijo != extension:
        raise ValueError(f"Esa ruta no termina en {extension}. No la guardo con otra extensión.")
    if not sufijo:
        limpia += extension
    return limpia


def _como_datos(valor):
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            return valor
    return valor


def _celda(valor):
    if valor is None or isinstance(valor, bool):
        return "" if valor is None else valor
    if isinstance(valor, (int, float)):
        return valor
    texto = str(valor).strip()
    if re.fullmatch(r"-?\d+", texto):
        try:
            return int(texto)
        except ValueError:
            return texto
    if re.fullmatch(r"-?\d+\.\d+", texto):
        try:
            return float(texto)
        except ValueError:
            return texto
    return texto


def _filas_de(valor) -> list[list]:
    datos = _como_datos(valor)
    if datos is None:
        return []
    if isinstance(datos, str):
        if "|" in datos or "\n" in datos:
            return []
        return [[_celda(parte)] for parte in re.split(r"[,;]", datos) if parte.strip()]
    if isinstance(datos, dict):
        return [[_celda(clave), _celda(item)] for clave, item in datos.items()]
    if not isinstance(datos, list):
        return [[_celda(datos)]]
    if datos and isinstance(datos[0], dict):
        columnas = []
        for fila in datos:
            if isinstance(fila, dict):
                for clave in fila:
                    if str(clave) not in columnas:
                        columnas.append(str(clave))
        filas = [[_celda(fila.get(col, "") if isinstance(fila, dict) else "") for col in columnas] for fila in datos]
        return {"columnas": [_celda(col) for col in columnas], "filas": filas}
    salida = []
    for fila in datos:
        if isinstance(fila, list):
            salida.append([_celda(item) for item in fila])
        else:
            salida.append([_celda(fila)])
    return salida


def _columnas_de(valor) -> list:
    datos = _como_datos(valor)
    if datos is None:
        return []
    if isinstance(datos, str):
        return [_celda(parte) for parte in re.split(r"[,;|]|\s+y\s+", datos) if parte.strip()]
    if isinstance(datos, list):
        return [_celda(item) for item in datos]
    return [_celda(datos)]


def _tabla_desde_texto(texto: str) -> list[dict] | None:
    lineas = [linea.strip() for linea in (texto or "").splitlines() if linea.strip()]
    if not any("|" in linea for linea in lineas):
        return None
    filas = []
    for linea in lineas:
        if re.fullmatch(r"[-| :]+", linea):
            continue
        filas.append([_celda(parte) for parte in linea.strip("|").split("|")])
    if not filas:
        return None
    return [{"nombre": "Datos", "columnas": filas[0], "filas": filas[1:]}]


def _nombre_hoja(nombre: str, indice: int, usados: set[str]) -> str:
    limpio = re.sub(r"[\[\]:*?/\\]", " ", (nombre or "").strip()) or f"Hoja {indice}"
    limpio = limpio[:31].strip() or f"Hoja {indice}"
    base = limpio
    n = 2
    while limpio.lower() in usados:
        sufijo = f" {n}"
        limpio = (base[: 31 - len(sufijo)] + sufijo).strip()
        n += 1
    usados.add(limpio.lower())
    return limpio


def _hojas_desde_txt(texto: str) -> list[dict]:
    """Pasa el texto a celdas. Si hay una lista de diccionarios, cada clave es una columna."""
    tabla = _tabla_desde_texto(texto)
    if tabla:
        return tabla
    datos = None
    try:
        arbol = ast.parse(texto or "")
    except SyntaxError:
        arbol = None
    if arbol is not None:
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.List):
                continue
            try:
                candidato = ast.literal_eval(nodo)
            except (ValueError, SyntaxError, MemoryError, TypeError):
                continue
            if isinstance(candidato, list) and candidato and all(isinstance(item, dict) for item in candidato):
                datos = candidato
                break
    if isinstance(datos, list):
        envuelto = _filas_de(datos)
        if isinstance(envuelto, dict) and (envuelto.get("columnas") or envuelto.get("filas")):
            return [{"nombre": "Datos", "columnas": envuelto.get("columnas") or [], "filas": envuelto.get("filas") or []}]
    lineas = [linea.strip() for linea in (texto or "").splitlines() if linea.strip()]
    if not lineas:
        raise ValueError("El texto no tiene contenido para las celdas.")
    return [{"nombre": "Datos", "columnas": ["Texto"], "filas": [[linea[:4000]] for linea in lineas[:500]]}]


def _plan_excel(ruta: str, hojas, columnas, filas, nombre_hoja: str, texto: str):
    """Elige el .xlsx junto al .txt encontrado. No escribe el archivo ni deja la acción pendiente."""
    if not (ruta or "").strip():
        raise ValueError("Falta la ruta del Excel.")
    origen = None
    destino = None
    nota = ""
    sufijo = Path((ruta or "").strip().strip('"').strip("'")).suffix.lower()
    if sufijo in {"", ".xlsx"}:
        try:
            candidato = rutas.resolver_ruta(_ruta_con_extension(ruta, ".xlsx", "Excel"))
        except rutas.PermisoRuta:
            raise
        except ValueError:
            candidato = None
        if candidato is not None and candidato.parent.is_dir():
            destino = candidato
    if destino is None:
        hallazgo = _ubicar(ruta, "txt")
        if isinstance(hallazgo, list):
            return hallazgo, None, ""
        if not isinstance(hallazgo, Path) or not hallazgo.is_file():
            raise FileNotFoundError(
                "No está en la ruta que armaste y no encontré esa carpeta ni un .txt en el Escritorio ni en el resto del equipo."
            )
        origen = hallazgo
        destino = rutas.resolver_ruta(str(origen.with_suffix(".xlsx")))
        nota = f"El .txt está en {_referencia(origen)}. "
    if destino.suffix.lower() != ".xlsx":
        raise ValueError("El archivo tiene que terminar en .xlsx.")
    if destino.exists():
        raise ValueError(f"Ese archivo ya existe: {rutas.describir(destino)}")
    hojas_norm = None
    if hojas or columnas or filas:
        hojas_norm = _normalizar_hojas(hojas, columnas, filas, nombre_hoja or "Datos", texto or "")
    if hojas_norm is None and origen is not None:
        contenido, _recortado = _leer_bytes(origen)
        hojas_norm = _hojas_desde_txt(contenido)
    if hojas_norm is None and (texto or "").strip():
        hojas_norm = _hojas_desde_txt(texto)
    if not hojas_norm:
        raise ValueError("El Excel necesita columnas y filas en celdas, no un párrafo suelto.")
    for hoja in hojas_norm:
        if len(hoja["columnas"]) > 50 or len(hoja["filas"]) > 500:
            raise ValueError("La tabla es demasiado grande.")
    return destino, hojas_norm, nota


def _normalizar_hojas(hojas, columnas, filas, nombre_hoja: str, texto: str) -> list[dict]:
    crudo = _como_datos(hojas)
    if isinstance(crudo, str):
        tabla = _tabla_desde_texto(crudo)
        crudo = tabla if tabla else None
    if isinstance(crudo, dict):
        crudo = [crudo]
    hojas_listas: list[dict] = []
    if isinstance(crudo, list):
        for item in crudo:
            if not isinstance(item, dict):
                continue
            cols = _columnas_de(item.get("columnas") or item.get("cabecera") or item.get("headers"))
            filas_hoja = _filas_de(item.get("filas") or item.get("rows") or item.get("datos"))
            if isinstance(filas_hoja, dict):
                if not cols:
                    cols = filas_hoja.get("columnas") or []
                filas_hoja = filas_hoja.get("filas") or []
            nombre = str(item.get("nombre") or item.get("hoja") or nombre_hoja or "")
            if cols or filas_hoja:
                hojas_listas.append({"nombre": nombre, "columnas": cols, "filas": filas_hoja})
    if not hojas_listas:
        cols = _columnas_de(columnas)
        filas_hoja = _filas_de(filas)
        if isinstance(filas_hoja, dict):
            if not cols:
                cols = filas_hoja.get("columnas") or []
            filas_hoja = filas_hoja.get("filas") or []
        if not cols and not filas_hoja:
            tabla = _tabla_desde_texto(texto)
            if tabla:
                return tabla
        if not cols and not filas_hoja and (texto or "").strip():
            cols = ["Texto"]
            filas_hoja = [[(texto or "").strip()[:4000]]]
        if cols or filas_hoja:
            hojas_listas.append({"nombre": nombre_hoja or "Datos", "columnas": cols, "filas": filas_hoja})
    if not hojas_listas:
        raise ValueError("El Excel necesita columnas y filas en celdas, no un párrafo suelto.")
    if len(hojas_listas) > 20:
        raise ValueError("Demasiadas hojas para un solo libro.")
    return hojas_listas


def _vista_hojas(hojas: list[dict]) -> str:
    lineas = []
    for hoja in hojas:
        lineas.append(f"Hoja {hoja.get('nombre') or 'Datos'}")
        columnas = hoja.get("columnas") or []
        filas = hoja.get("filas") or []
        if columnas:
            lineas.append(" | ".join(str(col) for col in columnas))
        for fila in filas[:30]:
            lineas.append(" | ".join(str(celda) for celda in fila))
        if len(filas) > 30:
            lineas.append(f"… {len(filas) - 30} filas más")
    return "\n".join(lineas)


def _preparar_destino(ruta: str, extension: str, etiqueta: str) -> Path:
    destino = rutas.resolver_ruta(_ruta_con_extension(ruta, extension, etiqueta))
    if destino.suffix.lower() != extension:
        raise ValueError(f"El archivo tiene que terminar en {extension}.")
    if destino.exists() and not destino.is_file():
        raise ValueError("Esa ruta no es un archivo.")
    if destino.exists():
        raise ValueError(f"Ese archivo ya existe: {rutas.describir(destino)}")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    return destino


@capturar
def proponer_crear_excel(
    ruta: str,
    hojas=None,
    columnas=None,
    filas=None,
    nombre_hoja: str = "",
    motivo: str = "",
    texto: str = "",
) -> dict:
    destino, hojas_norm, nota = _plan_excel(ruta, hojas, columnas, filas, nombre_hoja, texto)
    if isinstance(destino, list):
        return _respuesta_varias(destino)
    motivo_limpio = (motivo or "Pedido en el chat").strip()
    visible = rutas.describir(destino)
    vista = (
        f"Se creará un Excel (.xlsx) con los valores en celdas:\n{visible}\n\n"
        f"Motivo: {motivo_limpio}\n\n{_vista_hojas(hojas_norm)}"
    )
    pendiente = aprobacion.agregar(
        "excel",
        "Crear Excel",
        str(destino),
        vista,
        {"ruta": str(destino), "hojas": hojas_norm, "motivo": motivo_limpio},
    )
    return {
        "ok": True,
        "texto": f"{nota}Preparé el Excel {visible} con los datos en celdas. Aún no lo creé. Pulsa Aprobar o Rechazar.",
        "pendiente": pendiente,
    }


@capturar
def proponer_crear_pdf(ruta: str, texto: str, titulo: str = "", motivo: str = "") -> dict:
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("Falta el texto que irá dentro del PDF.")
    if len(texto) > LIMITE_ESCRITURA:
        raise ValueError("El texto es demasiado largo.")
    destino = _preparar_destino(ruta, ".pdf", "PDF")
    motivo_limpio = (motivo or "Pedido en el chat").strip()
    titulo_limpio = (titulo or "").strip()
    vista = (
        f"Se creará un PDF (empieza por %PDF), con párrafos legibles:\n{destino}\n\n"
        f"Motivo: {motivo_limpio}\n\n"
        + (f"Título: {titulo_limpio}\n\n" if titulo_limpio else "")
        + f"Texto:\n{texto}"
    )
    pendiente = aprobacion.agregar(
        "pdf",
        "Crear PDF",
        str(destino),
        vista,
        {"ruta": str(destino), "texto": texto, "titulo": titulo_limpio, "motivo": motivo_limpio},
    )
    return {
        "ok": True,
        "texto": f"Preparé el PDF {destino} con el texto en párrafos. Aún no lo creé. Pulsa Aprobar o Rechazar.",
        "pendiente": pendiente,
    }


def _crear_word(accion: dict) -> str:
    from docx import Document

    detalle = accion["detalle"]
    destino = rutas.resolver_ruta(detalle["ruta"])
    texto = detalle.get("texto")
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("El texto guardado no es válido.")
    if len(texto) > LIMITE_ESCRITURA:
        raise ValueError("El texto es demasiado largo.")
    if destino.suffix.lower() != ".docx":
        raise ValueError("El documento tiene que terminar en .docx.")
    if destino.exists() and not destino.is_file():
        raise ValueError("La ruta ya no es un archivo.")
    if destino.exists():
        raise ValueError("Ese documento ya existe. No lo sobrescribo.")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    padre.mkdir(parents=True, exist_ok=True)
    documento = Document()
    lineas = _parrafos(texto)
    if documento.paragraphs:
        documento.paragraphs[0].text = lineas[0]
        resto = lineas[1:]
    else:
        resto = lineas
    for linea in resto:
        documento.add_paragraph(linea)
    temporal = padre / f".{destino.name}.{uuid.uuid4().hex}.asistente.tmp"
    try:
        documento.save(str(temporal))
        _exigir_docx(temporal)
        temporal.replace(destino)
    finally:
        if temporal.exists():
            try:
                temporal.unlink()
            except OSError:
                pass
    return f"Creé el documento Word {destino}."


def _escribir_excel(temporal: Path, hojas: list[dict]) -> None:
    from openpyxl import Workbook

    libro = Workbook()
    usados: set[str] = set()
    for indice, hoja in enumerate(hojas, start=1):
        ws = libro.active if indice == 1 else libro.create_sheet()
        ws.title = _nombre_hoja(str(hoja.get("nombre") or ""), indice, usados)
        columnas = hoja.get("columnas") or []
        filas = hoja.get("filas") or []
        fila_inicial = 1
        if columnas:
            for col, valor in enumerate(columnas, start=1):
                ws.cell(1, col, valor)
            fila_inicial = 2
        for desplazamiento, fila in enumerate(filas):
            for col, valor in enumerate(fila, start=1):
                ws.cell(fila_inicial + desplazamiento, col, valor)
    libro.save(str(temporal))


def _crear_excel(accion: dict) -> str:
    detalle = accion["detalle"]
    destino = rutas.resolver_ruta(detalle["ruta"])
    hojas = detalle.get("hojas")
    if not isinstance(hojas, list) or not hojas:
        raise ValueError("El Excel guardado no tiene hojas.")
    if destino.suffix.lower() != ".xlsx":
        raise ValueError("El archivo tiene que terminar en .xlsx.")
    if destino.exists():
        raise ValueError("Ese Excel ya existe. No lo sobrescribo.")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    padre.mkdir(parents=True, exist_ok=True)
    temporal = padre / f".tmp-{uuid.uuid4().hex}.xlsx"
    try:
        _escribir_excel(temporal, hojas)
        _exigir_xlsx(temporal)
        temporal.replace(destino)
    finally:
        if temporal.exists():
            try:
                temporal.unlink()
            except OSError:
                pass
    return f"Creé el Excel {destino}."


def _escribir_pdf(temporal: Path, titulo: str, texto: str) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    fuente = "Helvetica"
    segoe = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
    if segoe.is_file():
        try:
            pdf.add_font("Segoe", "", str(segoe))
            fuente = "Segoe"
        except Exception:
            fuente = "Helvetica"
    if titulo.strip():
        pdf.set_font(fuente, size=16)
        pdf.multi_cell(0, 9, titulo.strip())
        pdf.ln(3)
    pdf.set_font(fuente, size=12)
    for linea in texto.splitlines() or [texto]:
        pdf.multi_cell(0, 7, linea if linea.strip() else " ")
        pdf.ln(1)
    pdf.output(str(temporal))


def _crear_pdf(accion: dict) -> str:
    detalle = accion["detalle"]
    destino = rutas.resolver_ruta(detalle["ruta"])
    texto = detalle.get("texto")
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("El texto guardado no es válido.")
    if len(texto) > LIMITE_ESCRITURA:
        raise ValueError("El texto es demasiado largo.")
    if destino.suffix.lower() != ".pdf":
        raise ValueError("El archivo tiene que terminar en .pdf.")
    if destino.exists():
        raise ValueError("Ese PDF ya existe. No lo sobrescribo.")
    padre = destino.parent
    if padre.exists() and not padre.is_dir():
        raise ValueError("La carpeta de destino no es válida.")
    padre.mkdir(parents=True, exist_ok=True)
    temporal = padre / f".tmp-{uuid.uuid4().hex}.pdf"
    try:
        _escribir_pdf(temporal, str(detalle.get("titulo") or ""), texto)
        _exigir_pdf(temporal)
        temporal.replace(destino)
    finally:
        if temporal.exists():
            try:
                temporal.unlink()
            except OSError:
                pass
    return f"Creé el PDF {destino}."


def _mover(accion: dict) -> str:
    import shutil

    detalle = accion["detalle"]
    origen = rutas.resolver_ruta(detalle["origen"])
    destino = rutas.resolver_ruta(detalle["destino"])
    if not origen.is_file():
        raise ValueError("El origen ya no es un archivo.")
    if destino.exists():
        raise ValueError("El destino ya existe. No lo sobrescribo.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(origen), str(destino))
    return f"Moví {origen} a {destino}."


aprobacion.registrar_ejecutor("escribir", _escribir)
aprobacion.registrar_ejecutor("borrar", _borrar)
aprobacion.registrar_ejecutor("mover", _mover)
aprobacion.registrar_ejecutor("carpeta", _crear_carpeta)
aprobacion.registrar_ejecutor("word", _crear_word)
aprobacion.registrar_ejecutor("excel", _crear_excel)
aprobacion.registrar_ejecutor("pdf", _crear_pdf)
