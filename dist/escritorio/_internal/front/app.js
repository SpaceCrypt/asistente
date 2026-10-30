const mensajes = document.querySelector("#mensajes");
const entrada = document.querySelector("#entrada");
const formulario = document.querySelector("#formulario");
const enviarchat = document.querySelector("#enviarchat");
const detenerchat = document.querySelector("#detenerchat");
let ocupado = false;
let controlador = null;
let termino = false;
let reloj = null;
let pulsoTimer = null;
let viendoArchivados = false;
let chatActivo = "";
let idsAvisados = {};
let modeloNombre = "—";
let fase = "inactivo";
let inicioReloj = 0;
let usoTurno = vacioTurno();
let usoSesion = {entrada: null, salida: null, segundos: null};
let gpuLinea = "";

function vacioTurno() {
  return {prompt_eval_count: null, eval_count: null, tokens_por_segundo: null, segundos: null};
}

function cifra(valor) {
  if (valor === null || valor === undefined || valor === "") {
    return "—";
  }
  return String(valor);
}

function mezclarUso(destino, origen) {
  if (!origen) {
    return;
  }
  ["prompt_eval_count", "eval_count", "tokens_por_segundo", "segundos"].forEach(function (clave) {
    if (origen[clave] !== null && origen[clave] !== undefined) {
      destino[clave] = origen[clave];
    }
  });
}

function textoTiempo() {
  if (fase === "generando") {
    return Math.floor((Date.now() - inicioReloj) / 1000) + " s";
  }
  if (usoTurno.segundos === null || usoTurno.segundos === undefined) {
    return "— s";
  }
  return usoTurno.segundos + " s";
}

function pintarMedidor() {
  document.querySelector("#usomodelo").textContent = modeloNombre || "—";
  document.querySelector("#usoestado").textContent = fase;
  document.querySelector("#usotiempo").textContent = textoTiempo();
  document.querySelector("#usogpu").textContent = gpuLinea || "";
  const frase = document.querySelector("#frasemodo");
  const lineaModo = document.querySelector("#usomodo");
  if (lineaModo && frase) {
    lineaModo.textContent = frase.textContent;
  }
  document.querySelector("#usocifras").textContent =
    "entrada " + cifra(usoTurno.prompt_eval_count) +
    " · salida " + cifra(usoTurno.eval_count) +
    " · " + cifra(usoTurno.tokens_por_segundo) + " tok/s";
  const segundos = usoSesion.segundos === null || usoSesion.segundos === undefined ? "—" : usoSesion.segundos;
  document.querySelector("#usosesion").textContent =
    "Sesión: entrada " + cifra(usoSesion.entrada) +
    " · salida " + cifra(usoSesion.salida) +
    " · " + segundos + " s";
  if (fase !== "generando") {
    document.querySelector("#usopulso").classList.remove("activo");
  }
}

function marcarPulso() {
  if (fase !== "generando") {
    return;
  }
  const pulso = document.querySelector("#usopulso");
  pulso.classList.add("activo");
  if (pulsoTimer) {
    clearTimeout(pulsoTimer);
  }
  pulsoTimer = setTimeout(function () {
    pulso.classList.remove("activo");
  }, 1200);
}

function iniciarReloj() {
  if (reloj) {
    clearInterval(reloj);
  }
  reloj = setInterval(pintarMedidor, 250);
}

function pararReloj() {
  if (reloj) {
    clearInterval(reloj);
    reloj = null;
  }
  pintarMedidor();
}

function mostrarAviso(texto) {
  const aviso = document.querySelector("#avisoaccion");
  aviso.hidden = false;
  aviso.textContent = texto;
}

function textoHola() {
  return "Hola, Andres. Soy tu asistente en este equipo. Dime con tus palabras lo que necesitas. Si hay que crear, cambiar, borrar, mover, hacer un Excel o un PDF, o enviar algo, lo dejo en Acciones pendientes y espero a que pulses Aprobar.";
}

function agregarMensaje(rol, texto, esError, id) {
  const burbuja = document.createElement("div");
  burbuja.className = "burbuja " + (rol === "usuario" ? "burbujausuario" : "burbujaasistente");
  if (esError) {
    burbuja.classList.add("burbujaerror");
  }
  burbuja._texto = texto;
  burbuja.textContent = texto;
  if (rol === "usuario" && id) {
    burbuja.setAttribute("datamensaje", id);
    const editar = document.createElement("button");
    editar.type = "button";
    editar.className = "botoneditar";
    editar.textContent = "Editar";
    editar.setAttribute("dataaccion", "editar");
    burbuja.appendChild(editar);
  }
  mensajes.appendChild(burbuja);
  mensajes.scrollTop = mensajes.scrollHeight;
  return burbuja;
}

function chip(texto) {
  const elemento = document.createElement("span");
  elemento.className = "chip";
  elemento.textContent = texto;
  return elemento;
}

function formatearHora(valor) {
  if (!valor) {
    return "";
  }
  const fecha = new Date(valor);
  if (Number.isNaN(fecha.getTime())) {
    return valor;
  }
  return fecha.toLocaleString("es");
}

function etiquetaAprobar(tipo) {
  if (tipo === "enviar_correo") {
    return "Aprobar y enviar";
  }
  if (tipo === "borrar") {
    return "Aprobar y borrar";
  }
  if (tipo === "mover") {
    return "Aprobar y mover";
  }
  if (tipo === "borrador_correo") {
    return "Aprobar borrador";
  }
  return "Aprobar";
}

function crearVista(item) {
  const detalle = item.detalle || {};
  if (item.tipo === "borrador_correo" || item.tipo === "enviar_correo") {
    const bloque = document.createElement("div");
    bloque.className = "vista";
    const campos = [
      ["Para", detalle.para],
      ["Asunto", detalle.asunto],
      ["Cuerpo", detalle.cuerpo],
    ];
    campos.forEach(function (campo) {
      const parrafo = document.createElement("p");
      const fuerte = document.createElement("strong");
      fuerte.textContent = campo[0] + ": ";
      parrafo.appendChild(fuerte);
      parrafo.appendChild(document.createTextNode(campo[1] || ""));
      bloque.appendChild(parrafo);
    });
    if (detalle.nota) {
      const nota = document.createElement("p");
      nota.className = "meta";
      nota.textContent = detalle.nota;
      bloque.appendChild(nota);
    }
    return bloque;
  }
  const pre = document.createElement("pre");
  pre.className = "vista";
  pre.textContent = item.vista || "";
  return pre;
}

function crearTarjeta(item, indice, total) {
  const tarjeta = document.createElement("article");
  tarjeta.className = "tarjeta";
  if (item.tipo === "enviar_correo") {
    tarjeta.classList.add("tarjetaenvio");
  }
  if (item.tipo === "borrar") {
    tarjeta.classList.add("tarjetaborrar");
  }
  const titulo = document.createElement("h3");
  const nombre = item.titulo || "Acción";
  titulo.textContent = total > 1 ? (indice + 1) + ". " + nombre : nombre;
  const resumen = document.createElement("p");
  resumen.className = "resumen";
  resumen.textContent = item.resumen || "";
  const acciones = document.createElement("div");
  acciones.className = "filaacciones";
  const aprobar = document.createElement("button");
  aprobar.type = "button";
  aprobar.className = item.tipo === "enviar_correo" || item.tipo === "borrar" ? "botonaprobar botonpeligro" : "botonaprobar";
  aprobar.setAttribute("dataaccion", "aprobar");
  aprobar.setAttribute("dataid", item.id);
  aprobar.textContent = etiquetaAprobar(item.tipo);
  const rechazar = document.createElement("button");
  rechazar.type = "button";
  rechazar.className = "botonrechazar";
  rechazar.setAttribute("dataaccion", "rechazar");
  rechazar.setAttribute("dataid", item.id);
  rechazar.textContent = "Rechazar";
  acciones.appendChild(aprobar);
  acciones.appendChild(rechazar);
  tarjeta.appendChild(titulo);
  tarjeta.appendChild(resumen);
  tarjeta.appendChild(crearVista(item));
  tarjeta.appendChild(acciones);
  return tarjeta;
}

let modeloAvisado = false;

function avisarSiFaltaModelo(estado) {
  if (modeloAvisado) {
    return;
  }
  modeloAvisado = true;
  const modelo = (estado && estado.modelo) || {};
  if (!modelo.configurado) {
    agregarMensaje("asistente", "El modelo no está disponible en este momento y no puedo interpretar una frase libre.");
  }
}

let firmaPendientes = "";
let firmaEstado = "";
let firmaActividad = "";

function revisarAvisos(lista) {
  lista.forEach(function (item) {
    if (!item || !item.id || !item.aviso_error || idsAvisados[item.id]) {
      return;
    }
    idsAvisados[item.id] = true;
    mostrarAviso("No pude mostrar el aviso de Windows para «" + (item.titulo || "acción") + "». Quedó en Acciones pendientes. " + item.aviso_error);
  });
}

function pintarPendientes(lista) {
  revisarAvisos(lista);
  const firma = JSON.stringify(lista);
  if (firma === firmaPendientes) {
    return;
  }
  firmaPendientes = firma;
  const contenedor = document.querySelector("#listapendientes");
  contenedor.replaceChildren();
  if (!lista.length) {
    const vacio = document.createElement("p");
    vacio.className = "vacio";
    vacio.textContent = "No hay acciones pendientes.";
    contenedor.appendChild(vacio);
    pintarAlerta(lista);
    return;
  }
  lista.forEach(function (item, indice) {
    contenedor.appendChild(crearTarjeta(item, indice, lista.length));
  });
  pintarAlerta(lista);
}

function pintarAlerta(lista) {
  const barra = document.querySelector("#alerta");
  if (!barra) {
    return;
  }
  barra.replaceChildren();
  if (!lista.length) {
    barra.hidden = true;
    return;
  }
  lista.forEach(function (item) {
    const tarjeta = document.createElement("div");
    tarjeta.className = "alertatarjeta";
    const texto = document.createElement("p");
    texto.textContent = (item.titulo || "Acción pendiente") + ". " + (item.resumen || "");
    const acciones = document.createElement("div");
    acciones.className = "filaacciones";
    const aprobar = document.createElement("button");
    aprobar.type = "button";
    aprobar.className = "botonaprobar";
    aprobar.setAttribute("dataaccion", "aprobar");
    aprobar.setAttribute("dataid", item.id);
    aprobar.textContent = "Aprobar";
    const rechazar = document.createElement("button");
    rechazar.type = "button";
    rechazar.className = "botonrechazar";
    rechazar.setAttribute("dataaccion", "rechazar");
    rechazar.setAttribute("dataid", item.id);
    rechazar.textContent = "Rechazar";
    acciones.appendChild(aprobar);
    acciones.appendChild(rechazar);
    tarjeta.appendChild(texto);
    tarjeta.appendChild(acciones);
    barra.appendChild(tarjeta);
  });
  barra.hidden = false;
}

function pintarEstado(datos) {
  const ultimo = datos.ultimo_turno || {};
  const firma = JSON.stringify({
    google: datos.google,
    modelo: datos.modelo,
    raices: datos.raices,
    borradores: datos.borradores_locales,
    tareas: datos.tareas_locales,
    linea: (datos.modelo || {}).linea,
    ultimo: ultimo,
  });
  const modelo = datos.modelo || {};
  if (modelo.nombre) {
    modeloNombre = modelo.nombre;
  }
  if (modelo.linea) {
    gpuLinea = modelo.linea;
  }
  if (!ocupado) {
    pintarMedidor();
  }
  if (firma === firmaEstado) {
    return;
  }
  firmaEstado = firma;
  const chips = document.querySelector("#chips");
  const google = datos.google || {};
  chips.replaceChildren(
    chip(google.conectado ? "Google conectado" : "Google sin conectar"),
    chip(modelo.configurado ? "Modelo " + (modelo.nombre || "conectado") : "Modelo no disponible")
  );
  const panel = document.querySelector("#panelestado");
  panel.replaceChildren();
  const uso = document.createElement("p");
  uso.textContent = modelo.linea || "";
  panel.appendChild(uso);
  const turno = document.createElement("p");
  turno.textContent =
    "Último turno: entrada " + cifra(ultimo.prompt_eval_count) +
    " · salida " + cifra(ultimo.eval_count) +
    " · " + cifra(ultimo.tokens_por_segundo) + " tok/s · " +
    (ultimo.segundos === null || ultimo.segundos === undefined ? "—" : ultimo.segundos) + " s.";
  panel.appendChild(turno);
  const lineaGoogle = document.createElement("p");
  lineaGoogle.textContent = google.conectado ? "Google conectado." : "Google sin conectar.";
  panel.appendChild(lineaGoogle);
  const raices = document.createElement("ul");
  raices.className = "raices";
  (datos.raices || []).forEach(function (raiz) {
    const item = document.createElement("li");
    const ruta = String(raiz.ruta || "").replace(/\\/g, "\\\u200b");
    item.textContent = raiz.nombre + " (" + (raiz.existe ? "disponible" : "no existe") + "): " + ruta;
    raices.appendChild(item);
  });
  panel.appendChild(raices);
  const extra = document.createElement("p");
  extra.className = "meta";
  extra.textContent = "Borradores guardados: " + (datos.borradores_locales || 0) + ". Tareas locales: " + (datos.tareas_locales || 0) + ".";
  panel.appendChild(extra);
}

function pintarActividad(items) {
  const firma = JSON.stringify(items);
  if (firma === firmaActividad) {
    return;
  }
  firmaActividad = firma;
  const lista = document.querySelector("#listaactividad");
  const nombres = {
    lectura: "Lectura",
    aprobada: "Aprobada",
    rechazada: "Rechazada",
    consulta: "Consulta",
    aviso: "Aviso",
    retroceso: "Retroceso",
  };
  lista.replaceChildren();
  if (!items.length) {
    const vacio = document.createElement("li");
    vacio.className = "vacio";
    vacio.textContent = "Todavía no hay lecturas ni acciones decididas.";
    lista.appendChild(vacio);
    return;
  }
  items.slice().reverse().forEach(function (item) {
    const fila = document.createElement("li");
    fila.textContent = formatearHora(item.ts) + " · " + (nombres[item.categoria] || item.categoria) + " · " + item.mensaje;
    lista.appendChild(fila);
  });
}

function aplicarChat(datos) {
  chatActivo = datos.activo || "";
  usoSesion = datos.uso || {entrada: null, salida: null, segundos: null};
  if (!ocupado && datos.ultimo) {
    usoTurno = {
      prompt_eval_count: datos.ultimo.prompt_eval_count,
      eval_count: datos.ultimo.eval_count,
      tokens_por_segundo: datos.ultimo.tokens_por_segundo,
      segundos: datos.ultimo.segundos,
    };
    fase = datos.ultimo.detenido ? "detenido" : "inactivo";
  }
  pintarMedidor();
  pintarChats(datos);
  if (!ocupado) {
    pintarMensajes(datos.mensajes || []);
  }
}

function pintarChats(datos) {
  const lista = document.querySelector("#listachats");
  const chats = (datos.chats || []).filter(function (chat) {
    return viendoArchivados ? chat.archivado : !chat.archivado;
  });
  lista.replaceChildren();
  if (!chats.length) {
    const vacio = document.createElement("p");
    vacio.className = "vacio";
    vacio.textContent = viendoArchivados ? "No hay chats archivados." : "No hay chats.";
    lista.appendChild(vacio);
    return;
  }
  chats.forEach(function (chat) {
    const ficha = document.createElement("article");
    ficha.className = "fichachat" + (chat.id === datos.activo && !chat.archivado || chat.id === chatActivo ? " chatactivo" : "");
    if (chat.id !== chatActivo) {
      ficha.classList.remove("chatactivo");
    }
    if (chat.id === chatActivo) {
      ficha.classList.add("chatactivo");
    }
    const titulo = document.createElement("button");
    titulo.type = "button";
    titulo.className = "fichatitulo";
    titulo.textContent = (chat.fijado ? "Fijado · " : "") + (chat.titulo || "Nuevo chat");
    titulo.setAttribute("datachat", chat.id);
    titulo.setAttribute("dataaccion", "activar");
    titulo.disabled = ocupado;
    const acciones = document.createElement("div");
    acciones.className = "fichaacciones";
    const fijar = document.createElement("button");
    fijar.type = "button";
    fijar.className = "fichabot";
    fijar.textContent = chat.fijado ? "Desfijar" : "Fijar";
    fijar.setAttribute("datachat", chat.id);
    fijar.setAttribute("dataaccion", "fijar");
    fijar.setAttribute("datavalor", chat.fijado ? "0" : "1");
    fijar.disabled = ocupado;
    const archivar = document.createElement("button");
    archivar.type = "button";
    archivar.className = "fichabot";
    archivar.textContent = chat.archivado ? "Desarchivar" : "Archivar";
    archivar.setAttribute("datachat", chat.id);
    archivar.setAttribute("dataaccion", "archivar");
    archivar.setAttribute("datavalor", chat.archivado ? "0" : "1");
    archivar.disabled = ocupado;
    const eliminar = document.createElement("button");
    eliminar.type = "button";
    eliminar.className = "fichabot";
    eliminar.textContent = "Eliminar";
    eliminar.setAttribute("datachat", chat.id);
    eliminar.setAttribute("dataaccion", "eliminar");
    eliminar.disabled = ocupado;
    acciones.appendChild(fijar);
    acciones.appendChild(archivar);
    acciones.appendChild(eliminar);
    ficha.appendChild(titulo);
    ficha.appendChild(acciones);
    lista.appendChild(ficha);
  });
}

function pintarMensajes(lista) {
  mensajes.replaceChildren();
  if (!lista.length) {
    agregarMensaje("asistente", textoHola());
    return;
  }
  lista.forEach(function (item) {
    const rol = item.role === "user" ? "usuario" : "asistente";
    agregarMensaje(rol, item.content || "", false, item.role === "user" ? item.id : "");
  });
}

async function cargarChats() {
  const respuesta = await fetch("/chats");
  const datos = await respuesta.json();
  if (!respuesta.ok || datos.ok === false) {
    return;
  }
  aplicarChat(datos);
}

async function refrescar() {
  try {
    const respuestas = await Promise.all([fetch("/pendientes"), fetch("/estado")]);
    const pendientes = await respuestas[0].json();
    const estado = await respuestas[1].json();
    pintarPendientes(pendientes.pendientes || []);
    pintarEstado(estado);
    pintarActividad(estado.actividad || []);
    avisarSiFaltaModelo(estado);
    const aviso = document.querySelector("#avisogoogle");
    if (aviso && aviso.textContent === "No pude actualizar el estado.") {
      aviso.textContent = "";
    }
  } catch (error) {
    const aviso = document.querySelector("#avisogoogle");
    if (aviso && !aviso.textContent) {
      aviso.textContent = "No pude actualizar el estado.";
    }
  }
}

let vigilando = null;

function vigilarBandeja(activo) {
  if (vigilando) {
    clearInterval(vigilando);
    vigilando = null;
  }
  if (!activo) {
    return;
  }
  const leer = function () {
    fetch("/pendientes")
      .then(function (respuesta) { return respuesta.json(); })
      .then(function (datos) { pintarPendientes(datos.pendientes || []); })
      .catch(function () {});
  };
  leer();
  vigilando = setInterval(leer, 500);
}

async function leerFlujo(respuesta, alEvento) {
  const lector = respuesta.body.getReader();
  const decodificador = new TextDecoder();
  let buffer = "";
  while (true) {
    const parte = await lector.read();
    if (parte.done) {
      break;
    }
    buffer += decodificador.decode(parte.value, {stream: true});
    let corte = buffer.indexOf("\n\n");
    while (corte >= 0) {
      const bloque = buffer.slice(0, corte);
      buffer = buffer.slice(corte + 2);
      const linea = bloque.split("\n").filter(function (item) {
        return item.startsWith("data:");
      }).map(function (item) {
        return item.slice(5).trim();
      }).join("");
      if (linea) {
        alEvento(JSON.parse(linea));
      }
      corte = buffer.indexOf("\n\n");
    }
  }
}

async function enviarFlujo(url, cuerpo, textoUsuario) {
  if (ocupado) {
    return;
  }
  const limpio = (textoUsuario || "").trim();
  if (!limpio) {
    return;
  }
  ocupado = true;
  termino = false;
  enviarchat.disabled = true;
  detenerchat.hidden = false;
  agregarMensaje("usuario", limpio);
  const espera = agregarMensaje("asistente", "Generando…");
  fase = "generando";
  inicioReloj = Date.now();
  usoTurno = vacioTurno();
  iniciarReloj();
  pintarMedidor();
  vigilarBandeja(true);
  controlador = new AbortController();
  let acumulado = "";
  const respaldo = setTimeout(function () {
    if (!termino && controlador) {
      controlador.abort();
    }
  }, 240000);
  try {
    const respuesta = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(cuerpo),
      signal: controlador.signal,
    });
    const tipo = respuesta.headers.get("content-type") || "";
    if (!tipo.includes("text/event-stream")) {
      const datos = await respuesta.json();
      termino = true;
      if (!respuesta.ok || datos.ok === false) {
        espera.textContent = datos.error || datos.detalle || "No pude completar la solicitud.";
        espera.classList.add("burbujaerror");
        fase = "inactivo";
      } else {
        espera.textContent = datos.respuesta || "(Sin respuesta)";
        fase = datos.detenido ? "detenido" : "inactivo";
        mezclarUso(usoTurno, datos.uso);
        if (datos.uso_sesion) {
          usoSesion = datos.uso_sesion;
        }
      }
      pintarMedidor();
    } else {
      await leerFlujo(respuesta, function (evento) {
        if (evento.tipo === "inicio" && evento.modelo) {
          modeloNombre = evento.modelo;
          pintarMedidor();
        }
        if (evento.tipo === "modelo") {
          marcarPulso();
          if (evento.delta) {
            acumulado += evento.delta;
            espera.textContent = acumulado;
            mensajes.scrollTop = mensajes.scrollHeight;
          }
          mezclarUso(usoTurno, evento.uso);
          pintarMedidor();
        }
        if (evento.tipo === "final") {
          termino = true;
          espera.textContent = evento.respuesta || acumulado || "(Sin respuesta)";
          fase = evento.detenido ? "detenido" : "inactivo";
          mezclarUso(usoTurno, evento.uso);
          if (evento.uso_sesion) {
            usoSesion = evento.uso_sesion;
          }
          if (evento.modelo) {
            modeloNombre = evento.modelo;
          }
          if (evento.gpu) {
            gpuLinea = evento.gpu;
          }
          pintarMedidor();
        }
        if (evento.tipo === "error") {
          termino = true;
          espera.textContent = evento.error || "No pude completar la solicitud.";
          espera.classList.add("burbujaerror");
          fase = "inactivo";
          pintarMedidor();
        }
      });
      if (!termino) {
        fase = "detenido";
        espera.textContent = "Detenido.";
        pintarMedidor();
      }
    }
  } catch (error) {
    if (error && error.name === "AbortError") {
      fase = "detenido";
      espera.textContent = "Detenido.";
    } else {
      fase = "inactivo";
      espera.textContent = "No pude hablar con el asistente en este equipo.";
      espera.classList.add("burbujaerror");
    }
    pintarMedidor();
  } finally {
    clearTimeout(respaldo);
    vigilarBandeja(false);
    ocupado = false;
    enviarchat.disabled = false;
    detenerchat.hidden = true;
    pararReloj();
    controlador = null;
    entrada.focus();
    await refrescar();
    try {
      await cargarChats();
    } catch (error) {
      mostrarAviso("No pude recargar la lista de chats.");
    }
  }
}

function enviarMensaje(texto) {
  const limpio = (texto || "").trim();
  if (!limpio || ocupado) {
    return;
  }
  entrada.value = "";
  return enviarFlujo("/chat", {mensaje: limpio, stream: true, chat_id: chatActivo || null}, limpio);
}

let decidiendo = false;

async function decidir(id, accion) {
  if (decidiendo) {
    return;
  }
  decidiendo = true;
  try {
    const respuesta = await fetch("/pendientes/" + id + "/" + accion, {method: "POST"});
    const datos = await respuesta.json();
    const texto = datos.mensaje || datos.error || "Listo.";
    agregarMensaje("asistente", texto, !respuesta.ok || datos.ok === false);
    await refrescar();
  } catch (error) {
    agregarMensaje("asistente", "No pude completar esa decisión.", true);
  } finally {
    decidiendo = false;
  }
}

function abrirEditor(burbuja) {
  if (ocupado || burbuja.querySelector("textarea")) {
    return;
  }
  const id = burbuja.getAttribute("datamensaje");
  const original = burbuja._texto || "";
  burbuja.replaceChildren();
  const editor = document.createElement("div");
  editor.className = "editor";
  const area = document.createElement("textarea");
  area.value = original;
  const fila = document.createElement("div");
  fila.className = "filaacciones";
  const guardar = document.createElement("button");
  guardar.type = "button";
  guardar.className = "botonprimario";
  guardar.textContent = "Guardar";
  guardar.setAttribute("dataaccion", "guardar");
  const cancelar = document.createElement("button");
  cancelar.type = "button";
  cancelar.className = "botonsecundario";
  cancelar.textContent = "Cancelar";
  cancelar.setAttribute("dataaccion", "cancelaredicion");
  fila.appendChild(guardar);
  fila.appendChild(cancelar);
  editor.appendChild(area);
  editor.appendChild(fila);
  burbuja.appendChild(editor);
  burbuja._texto = original;
  burbuja.setAttribute("datamensaje", id);
  area.focus();
}

function restaurarBurbuja(burbuja) {
  const id = burbuja.getAttribute("datamensaje");
  const texto = burbuja._texto || "";
  burbuja.replaceChildren();
  burbuja.textContent = texto;
  burbuja._texto = texto;
  if (id) {
    burbuja.setAttribute("datamensaje", id);
    const editar = document.createElement("button");
    editar.type = "button";
    editar.className = "botoneditar";
    editar.textContent = "Editar";
    editar.setAttribute("dataaccion", "editar");
    burbuja.appendChild(editar);
  }
}

formulario.addEventListener("submit", function (evento) {
  evento.preventDefault();
  enviarMensaje(entrada.value);
});

entrada.addEventListener("keydown", function (evento) {
  if (evento.key === "Enter" && !evento.shiftKey) {
    evento.preventDefault();
    formulario.requestSubmit();
  }
});

detenerchat.addEventListener("click", async function () {
  fase = "detenido";
  pintarMedidor();
  try {
    await fetch("/chat/detener", {method: "POST"});
  } catch (error) {
    mostrarAviso("No pude pedir que se detuviera la generación.");
  }
});

document.querySelector("#atajos").addEventListener("click", function (evento) {
  const boton = evento.target.closest("button");
  if (!boton) {
    return;
  }
  const texto = boton.getAttribute("dataejemplo");
  if (texto) {
    enviarMensaje(texto);
  }
});

document.querySelector("#listapendientes").addEventListener("click", function (evento) {
  const boton = evento.target.closest("button");
  if (!boton) {
    return;
  }
  const id = boton.getAttribute("dataid");
  const accion = boton.getAttribute("dataaccion");
  if (id && (accion === "aprobar" || accion === "rechazar")) {
    decidir(id, accion);
  }
});

document.querySelector("#avisoaccion").addEventListener("click", function () {
  document.querySelector("#avisoaccion").hidden = true;
});

document.querySelector("#nuevachat").addEventListener("click", async function () {
  if (ocupado) {
    return;
  }
  const respuesta = await fetch("/chats", {method: "POST"});
  const datos = await respuesta.json();
  if (respuesta.ok && datos.ok !== false) {
    aplicarChat(datos);
  }
});

document.querySelector("#verarchivados").addEventListener("click", async function () {
  viendoArchivados = !viendoArchivados;
  document.querySelector("#verarchivados").textContent = viendoArchivados ? "Chats" : "Archivados";
  try {
    await cargarChats();
  } catch (error) {
    mostrarAviso("No pude cargar los chats.");
  }
});

document.querySelector("#listachats").addEventListener("click", async function (evento) {
  const boton = evento.target.closest("button");
  if (!boton || ocupado) {
    return;
  }
  const id = boton.getAttribute("datachat");
  const accion = boton.getAttribute("dataaccion");
  if (!id || !accion) {
    return;
  }
  if (accion === "eliminar" && !window.confirm("¿Eliminar este chat? Los archivos que ya aprobaste en el disco no se borran.")) {
    return;
  }
  let respuesta;
  if (accion === "activar") {
    respuesta = await fetch("/chats/" + id + "/activar", {method: "POST"});
  } else if (accion === "fijar" || accion === "archivar") {
    respuesta = await fetch("/chats/" + id + "/" + accion, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({valor: boton.getAttribute("datavalor") === "1"}),
    });
  } else if (accion === "eliminar") {
    respuesta = await fetch("/chats/" + id, {method: "DELETE"});
  } else {
    return;
  }
  const datos = await respuesta.json();
  if (!respuesta.ok || datos.ok === false) {
    mostrarAviso(datos.error || "No pude actualizar el chat.");
    return;
  }
  if (datos.mensaje) {
    mostrarAviso(datos.mensaje);
  }
  aplicarChat(datos);
});

mensajes.addEventListener("click", function (evento) {
  const boton = evento.target.closest("button");
  if (!boton) {
    return;
  }
  const burbuja = boton.closest(".burbuja");
  if (!burbuja) {
    return;
  }
  const accion = boton.getAttribute("dataaccion");
  if (accion === "editar") {
    abrirEditor(burbuja);
    return;
  }
  if (accion === "cancelaredicion") {
    restaurarBurbuja(burbuja);
    return;
  }
  if (accion === "guardar") {
    const area = burbuja.querySelector("textarea");
    const texto = area ? area.value.trim() : "";
    const id = burbuja.getAttribute("datamensaje");
    if (!texto || !id || ocupado) {
      return;
    }
    let nodo = burbuja;
    while (nodo) {
      const siguiente = nodo.nextElementSibling;
      nodo.remove();
      nodo = siguiente;
    }
    enviarFlujo("/chat/editar", {
      mensaje_id: id,
      texto: texto,
      stream: true,
      chat_id: chatActivo || null,
    }, texto);
  }
});

document.querySelector("#conectargoogle").addEventListener("click", async function () {
  const aviso = document.querySelector("#avisogoogle");
  const boton = document.querySelector("#conectargoogle");
  boton.disabled = true;
  aviso.textContent = "Esperando la autorización en el navegador…";
  try {
    const respuesta = await fetch("/google/conectar", {method: "POST"});
    const datos = await respuesta.json();
    aviso.textContent = datos.mensaje || datos.error || "No se pudo conectar.";
    await refrescar();
  } catch (error) {
    aviso.textContent = "No pude completar la conexión con Google.";
  } finally {
    boton.disabled = false;
  }
});

let pintandoAjustes = false;

function pintarAjustes(datos) {
  pintandoAjustes = true;
  const modelo = document.querySelector("#seleccionmodelo");
  const modo = document.querySelector("#seleccionmodo");
  const nota = document.querySelector("#notamodelos");
  const detalle = document.querySelector("#detallemode");
  const frase = document.querySelector("#frasemodo");
  const aviso = document.querySelector("#avisomodo");
  const elegido = (datos.modelos || []).filter(function (item) {
    return item.elegido;
  })[0];
  modelo.replaceChildren();
  (datos.modelos || []).forEach(function (item) {
    const opcion = document.createElement("option");
    opcion.value = item.nombre;
    opcion.textContent = item.nombre + " · " + (item.etiqueta || "") + " · " + (item.disco || "");
    modelo.appendChild(opcion);
  });
  if (datos.modelo) {
    modelo.value = datos.modelo;
  }
  if (datos.modo) {
    modo.value = datos.modo;
  }
  nota.textContent = datos.nota || "No hay más modelos instalados.";
  detalle.textContent = elegido ? elegido.frase : "";
  aviso.textContent = datos.aviso || "";
  const activo = (datos.modos || []).filter(function (item) {
    return item.activo;
  })[0];
  const titulo = activo ? activo.titulo : "Solo RAM";
  frase.textContent = "Modo activo: " + titulo + ". " + (datos.frase || "");
  if (datos.modelo) {
    modeloNombre = datos.modelo;
  }
  pintandoAjustes = false;
  pintarMedidor();
}

async function cargarModelos() {
  const respuesta = await fetch("/modelos");
  const datos = await respuesta.json();
  if (respuesta.ok && datos.ok !== false) {
    pintarAjustes(datos);
  }
}

async function guardarAjuste(parcial) {
  if (ocupado) {
    return;
  }
  try {
    const respuesta = await fetch("/ajustes", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(parcial),
    });
    const datos = await respuesta.json();
    if (!respuesta.ok || datos.ok === false) {
      mostrarAviso(datos.error || "No pude guardar la elección.");
      await cargarModelos();
      return;
    }
    pintarAjustes(datos);
  } catch (error) {
    mostrarAviso("No pude guardar la elección.");
  }
}

document.querySelector("#seleccionmodelo").addEventListener("change", function (evento) {
  if (pintandoAjustes || !evento.target.value) {
    return;
  }
  guardarAjuste({modelo: evento.target.value});
});

document.querySelector("#seleccionmodo").addEventListener("change", function (evento) {
  if (pintandoAjustes || !evento.target.value) {
    return;
  }
  guardarAjuste({modo: evento.target.value});
});

document.querySelector("#alerta").addEventListener("click", function (evento) {
  const boton = evento.target.closest("button");
  if (!boton) {
    return;
  }
  const id = boton.getAttribute("dataid");
  const accion = boton.getAttribute("dataaccion");
  if (id && (accion === "aprobar" || accion === "rechazar")) {
    decidir(id, accion);
  }
});

async function limpiarDatos(ruta, pregunta) {
  if (!window.confirm(pregunta)) {
    return;
  }
  try {
    const respuesta = await fetch(ruta, {method: "POST"});
    const datos = await respuesta.json();
    if (!respuesta.ok || datos.ok === false) {
      mostrarAviso(datos.error || "No pude limpiar esos datos.");
      return;
    }
    firmaPendientes = "";
    firmaActividad = "";
    firmaEstado = "";
    await refrescar();
  } catch (error) {
    mostrarAviso("No pude limpiar esos datos.");
  }
}

document.querySelector("#limpiarpendientes").addEventListener("click", function () {
  limpiarDatos("/limpiar/pendientes", "¿Vaciar las acciones pendientes? No se borra el código ni lo aprendido.");
});

document.querySelector("#limpiaractividad").addEventListener("click", function () {
  limpiarDatos("/limpiar/actividad", "¿Vaciar la actividad reciente y el último turno? Lo aprendido se queda.");
});

document.querySelector("#olvidaraprendido").addEventListener("click", function () {
  limpiarDatos("/limpiar/aprendido", "¿Olvidar lo aprendido? Esto borra datos/memoria.json y no se deshace.");
});

pintarMedidor();
cargarModelos().catch(function () {
  document.querySelector("#notamodelos").textContent = "No pude leer los modelos instalados.";
});
cargarChats().catch(function () {
  agregarMensaje("asistente", textoHola());
});
refrescar();
setInterval(refrescar, 4000);
