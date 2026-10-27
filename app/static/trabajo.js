// Seguimiento de un trabajo: consulta el estado mientras siga activo y muestra las preguntas
// que el flujo le hace a una persona (modal, captcha, revisar dirección, confirmar "Facturar").
import { api } from "./api.js";
import { el, vaciar } from "./dom.js";

const ACTIVOS = ["en_cola", "ejecutando", "esperando_respuesta"];
const INTERVALO_MS = 1500;
const TITULO = "Facturación";
const ETIQUETAS = { facturar: "Facturar", cancelar: "Cancelar", continuar: "Continuar", cerrar: "Cerrar",
  reintentar: "Reintentar" };

function urlArchivo(id, nombre) {
  return `/facturas/${encodeURIComponent(id)}/archivos/${encodeURIComponent(nombre)}`;
}

function resumenParaConfirmar(datos) {
  const d = datos || {};
  const fiscales = d.datos_fiscales_portal || {};
  const lineas = [
    ["TC", d.tc], ["Folio", d.folio], ["Ticket", d.ticket], ["Uso CFDI", d.uso_cfdi || fiscales["Uso CFDI"]],
    ["Forma de pago", d.forma_pago], ["RFC", fiscales.RFC],
  ].filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`);
  return ["¿Facturar? Este paso no se puede deshacer.", "", ...lineas].join("\n");
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

export function montar(contenedor, id) {
  let activo = true;
  let temporizador = null;
  let cuentaRegresiva = null;
  let preguntaVista = null;
  let enCurso = false;
  let repetir = false;

  const estado = el("span", { clase: "estado" }, "…");
  const facturado = el("p", { clase: "tenue" });
  const mensaje = el("div", { role: "alert" });
  const pregunta = el("div");
  const resultado = el("div");
  const cancelar = el("button", { type: "button", clase: "peligro", hidden: true, on: { click: cancelarTrabajo } },
    "Cancelar trabajo");
  const eventos = el("ul", { clase: "eventos" });
  contenedor.append(
    el("div", { clase: "tarjeta" }, el("h1", {}, "Trabajo ", el("code", {}, id)),
      el("p", {}, "Estado: ", estado), facturado, mensaje, cancelar),
    pregunta, resultado,
    el("details", { clase: "tarjeta" }, el("summary", {}, "Eventos"), eventos));

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
        estado.textContent = "perdido";
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

  function pintar(t) {
    estado.textContent = t.estado;
    facturado.textContent = t.facturado ? "Ya se dio el clic en Facturar." : "";
    cancelar.hidden = t.facturado || !ACTIVOS.includes(t.estado);
    vaciar(eventos, (t.eventos || []).map((e) => el("li", {}, `${e.hora.slice(11, 19)} · ${e.mensaje}`)));
    pintarPregunta(t);
    pintarResultado(t);
  }

  function pintarPregunta(t) {
    const p = t.estado === "esperando_respuesta" ? t.pregunta : null;
    if (!p) {
      if (preguntaVista !== null) {
        preguntaVista = null;
        clearInterval(cuentaRegresiva);
        vaciar(pregunta);
      }
      document.title = TITULO;
      return;
    }
    document.title = "⚠ Pregunta pendiente";
    if (preguntaVista === p.id) return;      // misma pregunta: no se reinician botones ni reloj
    preguntaVista = p.id;
    beep();
    const reloj = el("p", { clase: "tenue" });
    let restante = p.expira_en_s;
    const tic = () => {
      const m = Math.floor(restante / 60);
      const s = String(restante % 60).padStart(2, "0");
      reloj.textContent = `Se cancela sin facturar si no respondes en ${m}:${s}.`;
      restante = Math.max(0, restante - 1);
    };
    tic();
    clearInterval(cuentaRegresiva);
    cuentaRegresiva = setInterval(tic, 1000);
    const botones = p.opciones.map((opcion) => el("button", {
      type: "button", clase: opcion === "cancelar" || opcion === "cerrar" ? "secundario" : undefined,
      on: { click: () => responder(p, opcion, botones) },
    }, ETIQUETAS[opcion] || opcion));
    vaciar(pregunta, el("div", { clase: "tarjeta pregunta" },
      el("h2", {}, "El trabajo necesita tu respuesta"),
      el("p", { clase: "texto-pregunta" }, p.texto),
      p.captura ? el("img", { src: urlArchivo(t.id, p.captura), alt: "Captura del portal", clase: "captura" }) : null,
      reloj, botones));
  }

  function pintarResultado(t) {
    if (ACTIVOS.includes(t.estado)) return vaciar(resultado);
    const hijos = [];
    if (t.estado === "completado") {
      if (t.archivos.length) {
        hijos.push(el("p", { clase: "ok" }, "Listo. Archivos:"),
          el("ul", {}, t.archivos.map((n) => el("li", {}, el("a", { href: urlArchivo(t.id, n), download: n }, n)))));
      } else if (t.tipo === "factura") {
        hijos.push(el("p", { clase: "ok" }, "Listo. La factura llegará a tu correo."));
      } else {
        hijos.push(el("p", { clase: "ok" }, "Terminado."));
      }
    } else {
      hijos.push(el("p", { clase: "error" }, t.error || `El trabajo terminó como ${t.estado}.`));
      const ultima = t.capturas[t.capturas.length - 1];
      if (ultima) hijos.push(el("img", { src: urlArchivo(t.id, ultima), alt: "Última captura", clase: "captura" }));
    }
    vaciar(resultado, el("div", { clase: "tarjeta" }, el("h2", {}, "Resultado"), hijos));
  }

  async function responder(p, opcion, botones) {
    if (opcion === "facturar" && !window.confirm(resumenParaConfirmar(p.datos))) return;
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
    clearInterval(cuentaRegresiva);
    if (document.title !== TITULO) document.title = TITULO;
  }

  return detener;
}
