// Seguimiento de un trabajo: consulta el estado mientras siga activo y muestra las preguntas
// que el flujo le hace a una persona (modal, captcha, revisar dirección, confirmar "Facturar").
import { api } from "../api.js";
import { cerrarDialogo, confirmar } from "../dialogo.js";
import { el, icono, vaciar } from "../dom.js";
import { reproducir } from "../movimiento.js";

const ACTIVOS = ["en_cola", "ejecutando", "esperando_respuesta"];
const INTERVALO_MS = 1500;
const TITULO = "TicketIA";
const ETIQUETAS = { facturar: "Facturar", cancelar: "Cancelar", continuar: "Continuar", cerrar: "Cerrar",
  reintentar: "Reintentar" };
const ESTADOS = {
  en_cola: ["En cola", ""], ejecutando: ["En curso", ""], esperando_respuesta: ["Esperando tu respuesta", "alerta"],
  completado: ["Completado", "ok"], cancelado: ["Cancelado", "error"], error: ["Error", "error"],
};
const TITULOS = { factura: "Tu factura", consulta: "Consulta de factura", inspeccion: "Diagnóstico del portal" };
const PREGUNTAS = {
  confirmar_facturar: ["¿Facturar este ticket?", "Revisa y confirma la factura"],
  revisar_direccion: ["Los datos del portal no coinciden", "Decide si continuar con lo que muestra el portal"],
  modal: ["El portal mostró un mensaje", "Léelo y decide cómo seguir"],
  captcha: ["El portal pidió un captcha", "TicketIA no resuelve captchas: reintenta o cancela"],
};
const PASOS = [
  ["Llenando el portal", "Validando el ticket y tus datos fiscales"],
  ["Esperando tu respuesta", ""],
  ["Emitiendo la factura", "Ya diste el clic en Facturar"],
  ["Factura emitida", ""],
];
const CIRCUNFERENCIA = 62.83;   // 2πr con r = 10

// Paso de la línea de tiempo a partir del trabajo (sin memoria de consultas anteriores).
export function pasoActual(t) {
  if (t.estado === "completado") return { indice: 3, estado: "hecho" };
  if (t.estado === "cancelado" || t.estado === "error") {
    const pregunto = (t.eventos || []).some((e) => e.mensaje.startsWith("Pregunta '"));
    return { indice: t.facturado ? 2 : pregunto ? 1 : 0, estado: "error" };
  }
  if (t.facturado) return { indice: 2, estado: "actual" };
  if (t.estado === "esperando_respuesta") return { indice: 1, estado: "actual" };
  return { indice: 0, estado: "actual" };
}

// "m:ss" que faltan hasta expiraEn (ISO). Nunca negativo.
export function formatoRestante(expiraEn, ahora) {
  const segundos = Math.max(0, Math.floor((Date.parse(expiraEn) - ahora) / 1000));
  return `${Math.floor(segundos / 60)}:${String(segundos % 60).padStart(2, "0")}`;
}

function urlArchivo(id, nombre) {
  return `/facturas/${encodeURIComponent(id)}/archivos/${encodeURIComponent(nombre)}`;
}

function confirmacion(datos) {
  // Texto del diálogo antes del clic irreversible: monto si lo hay, razón social y RFC del portal.
  const d = datos || {};
  const fiscales = d.datos_fiscales_portal || {};
  const quien = [fiscales["Razón social"], fiscales.RFC && `RFC ${fiscales.RFC}`].filter(Boolean).join(", ");
  return {
    titulo: d.total ? `¿Facturar $${d.total}?` : "¿Facturar este ticket?",
    texto: `${quien ? `Se emitirá el CFDI a nombre de ${quien}. ` : ""}Este paso no se puede deshacer.`,
    aceptar: "Sí, facturar",
    cancelar: "Revisar de nuevo",
  };
}

function resumen(datos) {
  // Lo que el usuario necesita ver antes de decidir: datos fiscales que quedaron en el portal y el ticket.
  const d = datos || {};
  const fiscales = d.datos_fiscales_portal || {};
  const filas = [
    ["RFC", fiscales.RFC], ["Razón social", fiscales["Razón social"]], ["TC", d.tc], ["Folio", d.folio],
    ["Fecha", d.fecha_portal], ["Uso CFDI", d.uso_cfdi_portal || d.uso_cfdi || fiscales["Uso CFDI"]],
    ["Forma de pago", d.forma_pago], ["Total", d.total && `$${d.total}`],
  ].filter(([, v]) => v);
  if (!filas.length) return null;
  return el("dl", { clase: "resumen" }, filas.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v))]));
}

function diferencias(datos) {
  // {"RAZON_SOCIAL": {"esperado": "...", "en_portal": "..."}}: lo configurado contra lo del portal.
  const filas = Object.entries(datos || {}).map(([campo, d]) => el("li", {},
    el("strong", {}, campo), el("span", {}, `Tus datos: ${d.esperado ?? "—"}`),
    el("span", {}, `Portal: ${d.en_portal ?? "(vacío)"}`)));
  return filas.length ? el("ul", { clase: "diferencias" }, filas) : null;
}

function beep() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    osc.frequency.value = 880;
    osc.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.2);
    osc.onended = () => ctx.close();
  } catch { /* sin audio: queda el aviso en el título */ }
}

function svg(tag, atributos) {
  const nodo = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(atributos)) nodo.setAttribute(k, v);
  return nodo;
}

export function montar(contenedor, id) {
  let activo = true;
  let temporizador = null;
  let reloj = null;
  let preguntaVista = null;
  let enCurso = false;
  let repetir = false;
  let ultimoResumen = null;

  const titulo = el("h1", {}, "Trabajo");
  const estado = el("span", { clase: "pill estado", "data-estado": "", role: "status" }, "…");
  // Lectores de pantalla: se anuncia cada pregunta nueva una vez (no el reloj, que cambia cada segundo).
  const anuncio = el("p", { clase: "solo-lector", role: "status" });
  const mensaje = el("div", { clase: "mensaje", role: "alert" });
  const pasos = el("ol", { clase: "pasos" }, PASOS.map(([nombre], i) =>
    el("li", { "data-paso": String(i) }, el("span", { clase: "paso-icono" }),
      el("div", {}, el("b", {}, nombre), el("small", {})))));
  const pregunta = el("div", { clase: "pregunta-zona" });
  const resultado = el("div");
  const cancelar = el("button", { type: "button", clase: "btn peligro chico", hidden: true, on: { click: cancelarTrabajo } },
    "Cancelar trabajo");
  const eventos = el("ul", { clase: "actividad" });

  contenedor.append(el("div", { clase: "trabajo" },
    el("div", { clase: "trabajo-encabezado" },
      el("div", {}, titulo, el("code", { clase: "tenue" }, `Trabajo ${id}`)),
      el("div", { clase: "trabajo-acciones" }, estado, cancelar)),
    el("div", { clase: "trabajo-cuerpo" },
      el("section", { clase: "tarjeta trabajo-principal" }, pasos, mensaje, anuncio, pregunta, resultado),
      el("aside", { clase: "trabajo-lateral" },
        el("section", { clase: "tarjeta" }, el("h2", {}, "Actividad"), eventos),
        el("p", { clase: "tarjeta nota tenue" },
          "Si cierras esta pestaña, el trabajo sigue en el servidor. Vuelve con este enlace para ver el resultado.")))));

  consultar();

  function programar() {
    if (activo && !repetir) temporizador = setTimeout(consultar, INTERVALO_MS);
  }

  async function consultar() {
    // Una sola consulta a la vez: si llega otra (p. ej. tras responder), se repite al terminar.
    if (enCurso) {
      repetir = true;
      return;
    }
    enCurso = true;
    clearTimeout(temporizador);
    temporizador = null;
    try {
      await consultarUnaVez();
    } finally {
      enCurso = false;
    }
    if (repetir && activo) {
      repetir = false;
      consultar();
    }
  }

  async function consultarUnaVez() {
    let t;
    try {
      t = await api("GET", `/facturas/${encodeURIComponent(id)}`);
    } catch (e) {
      if (!activo) return;
      if (e.status === 404) {
        detener();
        mensaje.textContent = "El servicio se reinició y el trabajo se perdió. Si ya habías confirmado "
          + "Facturar, revisa tu correo o usa Consultar.";
        pintarEstado("error", "Perdido");
        pasos.hidden = true;
        return;
      }
      if (e.status === 401) return detener();     // api() ya mandó al login
      mensaje.textContent = e.message;              // p. ej. sin conexión: se reintenta
      return programar();
    }
    if (!activo) return;
    mensaje.textContent = "";
    pintar(t);
    if (ACTIVOS.includes(t.estado)) programar();
    else detener();
  }

  function pintarEstado(clave, texto) {
    const [nombre, clase] = ESTADOS[clave] || [clave, ""];
    estado.dataset.estado = clave;
    estado.className = `pill estado ${clase}`.trim();
    estado.textContent = texto || nombre;
  }

  function pintar(t) {
    titulo.textContent = TITULOS[t.tipo] || "Trabajo";
    pintarEstado(t.estado);
    cancelar.hidden = t.facturado || !ACTIVOS.includes(t.estado);
    pasos.hidden = t.tipo !== "factura";
    pintarPasos(t);
    vaciar(eventos, (t.eventos || []).slice().reverse().map((e) =>
      el("li", {}, el("time", {}, e.hora.slice(11, 19)), el("span", {}, e.mensaje))));
    pintarPregunta(t);
    pintarResultado(t);
  }

  function pintarPasos(t) {
    const { indice, estado: tipo } = pasoActual(t);
    pasos.querySelectorAll("li").forEach((li, i) => {
      const clase = i < indice ? "hecho" : i === indice ? tipo : "";
      if (li.className === clase) return;
      li.className = clase;
      vaciar(li.querySelector(".paso-icono"), clase === "hecho" ? icono("check")
        : clase === "error" ? icono("cerrar") : clase === "actual" ? icono("punto") : null);
    });
    const p = t.pregunta;
    pasos.querySelector('[data-paso="1"] small').textContent = p ? (PREGUNTAS[p.tipo] || [])[1] || "" : "";
    pasos.querySelector('[data-paso="3"] small').textContent = t.estado === "completado"
      ? (t.archivos.length ? "Descarga tus archivos abajo" : "Llegará a tu correo") : "";
  }

  function pintarPregunta(t) {
    const p = t.estado === "esperando_respuesta" ? t.pregunta : null;
    if (!p || p.id !== preguntaVista) cerrarDialogo();     // la pregunta ya no es la que se estaba confirmando
    if (!p) {
      if (preguntaVista !== null) {
        preguntaVista = null;
        clearInterval(reloj);
        vaciar(pregunta);
      }
      document.title = TITULO;
      return;
    }
    document.title = "⚠ Pregunta pendiente";
    if (preguntaVista === p.id) return;      // misma pregunta: no se reinician botones ni reloj
    preguntaVista = p.id;
    beep();
    if (p.tipo === "confirmar_facturar") ultimoResumen = p.datos;

    const restante = el("span", {});
    const arco = svg("circle", { class: "avance", cx: "12", cy: "12", r: "10", fill: "none", "stroke-width": "3",
      "stroke-dasharray": String(CIRCUNFERENCIA) });
    const anillo = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
    anillo.append(svg("circle", { class: "pista", cx: "12", cy: "12", r: "10", fill: "none", "stroke-width": "3" }), arco);
    const total = p.expira_en_s || 600;
    const tic = () => {
      const texto = p.expira_en ? formatoRestante(p.expira_en, Date.now()) : "";
      const faltan = p.expira_en ? Math.max(0, (Date.parse(p.expira_en) - Date.now()) / 1000) : total;
      restante.textContent = texto ? `${texto} para responder` : "";
      arco.setAttribute("stroke-dashoffset", String(CIRCUNFERENCIA * (1 - faltan / total)));
    };
    tic();
    clearInterval(reloj);
    reloj = setInterval(tic, 1000);

    const botones = p.opciones.map((opcion) => el("button", {
      type: "button", clase: opcion === "cancelar" || opcion === "cerrar" ? "btn sec" : "btn",
      on: { click: () => responder(p, opcion, botones) },
    }, ETIQUETAS[opcion] || opcion));
    const capturas = t.capturas.slice().reverse();
    if (p.captura) capturas.sort((a, b) => (a === p.captura ? -1 : b === p.captura ? 1 : 0));
    const [titular] = PREGUNTAS[p.tipo] || ["El trabajo necesita tu respuesta"];
    const tarjeta = el("div", { clase: "pregunta" },
      el("div", { clase: "pregunta-encabezado" }, el("h2", {}, titular),
        el("div", { clase: "reloj", "aria-hidden": "true" }, anillo, restante)),
      el("p", { clase: "texto-pregunta" }, p.texto),
      p.tipo === "revisar_direccion" ? diferencias(p.datos) : resumen(p.datos),
      capturas.length ? el("div", { clase: "capturas" }, capturas.slice(0, 6).map((n) =>
        el("a", { href: urlArchivo(t.id, n), target: "_blank", rel: "noopener", clase: n === p.captura ? "principal" : undefined },
          el("img", { src: urlArchivo(t.id, n), alt: `Captura del portal: ${n}`, loading: "lazy" })))) : null,
      el("div", { clase: "acciones" }, botones));
    vaciar(pregunta, tarjeta);
    anuncio.textContent = `Nueva pregunta: ${titular}`;
  }

  function pintarResultado(t) {
    if (ACTIVOS.includes(t.estado)) return vaciar(resultado);
    if (resultado.dataset.estado === t.estado) return;
    resultado.dataset.estado = t.estado;
    const hijos = [];
    if (t.estado === "completado") {
      const fiscales = (ultimoResumen && ultimoResumen.datos_fiscales_portal) || {};
      const lineas = [["RFC", fiscales.RFC], ["Total", ultimoResumen && ultimoResumen.total && `$${ultimoResumen.total}`],
        ["Archivos", t.archivos.length ? String(t.archivos.length) : null]].filter(([, v]) => v);
      if (t.tipo === "factura") {
        hijos.push(
          el("div", { clase: "papel" }, el("div", { clase: "comprobante ticket-termico" },
            el("div", { clase: "comprobante-titulo" }, "CFDI 4.0 · FACTURADO"),
            lineas.map(([k, v]) => el("div", { clase: "renglon" }, el("span", {}, k), el("span", {}, v))))),
          el("span", { clase: "sello-ok" }, icono("check")),
          el("h2", {}, "Factura emitida"),
          el("p", { clase: "tenue" }, t.archivos.length ? "Descarga el XML y el PDF:" : "Llegará a tu correo en unos minutos."));
      } else {
        hijos.push(el("h2", {}, "Terminado"));
      }
      if (t.archivos.length) {
        hijos.push(el("ul", { clase: "archivos" }, t.archivos.map((n) =>
          el("li", {}, el("a", { href: urlArchivo(t.id, n), download: n }, n)))));
      }
      if (t.tipo === "factura") {
        hijos.push(el("a", { clase: "btn sec", href: "#/nueva" }, "Facturar otro ticket"));
      }
    } else {
      hijos.push(el("h2", {}, t.estado === "cancelado" ? "No se facturó" : "Algo salió mal"),
        el("p", { clase: "error" }, t.error || `El trabajo terminó como ${t.estado}.`));
      const ultima = t.capturas[t.capturas.length - 1];
      if (ultima) {
        hijos.push(el("a", { href: urlArchivo(t.id, ultima), target: "_blank", rel: "noopener" },
          el("img", { src: urlArchivo(t.id, ultima), alt: "Última captura del portal", clase: "captura" })));
      }
      hijos.push(el("a", { clase: "btn sec", href: "#/nueva" }, "Volver a empezar"));
    }
    const caja = el("div", { clase: `resultado ${t.estado}` }, hijos);
    vaciar(resultado, caja);
    if (t.estado === "completado") reproducir(caja, "mostrar");
  }

  async function responder(p, opcion, botones) {
    if (opcion === "facturar" && !(await confirmar(confirmacion(p.datos)))) return;
    if (!activo || preguntaVista !== p.id) return;      // la pregunta cambió mientras se confirmaba
    botones.forEach((b) => { b.disabled = true; });
    try {
      await api("POST", `/facturas/${encodeURIComponent(id)}/respuesta`, { pregunta_id: p.id, respuesta: opcion });
    } catch (e) {
      if (e.status !== 409) {
        mensaje.textContent = e.message;
        botones.forEach((b) => { b.disabled = false; });
        return;
      }
    }
    ahora();
  }

  async function cancelarTrabajo() {
    cancelar.disabled = true;
    try {
      await api("POST", `/facturas/${encodeURIComponent(id)}/cancelar`);
    } catch (e) {
      mensaje.textContent = e.message;
    }
    cancelar.disabled = false;
    ahora();
  }

  function ahora() {
    // Consultar de inmediato tras una acción, sin duplicar el ciclo de consultas.
    if (activo) consultar();
  }

  function detener() {
    activo = false;
    clearTimeout(temporizador);
    clearInterval(reloj);
    cerrarDialogo();
    if (document.title !== TITULO) document.title = TITULO;
  }

  return detener;
}
