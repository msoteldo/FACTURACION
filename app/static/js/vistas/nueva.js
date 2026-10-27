// Nueva factura: foto -> datos leídos por Gemini -> formulario editable -> crear trabajo.
import { api } from "../api.js";
import { el, icono, vaciar } from "../dom.js";
import { reproducir } from "../movimiento.js";

const MAX_BYTES = 10 * 1024 * 1024;

const COMERCIOS = {
  walmart: "Sam's Club / Walmart",
  oxxo: "OXXO",
  heb: "HEB",
};

const FORMAS_PAGO = [["01", "Efectivo"], ["04", "Tarjeta de crédito"], ["28", "Tarjeta de débito"],
  ["05", "Monedero electrónico"]];
const USOS_CFDI = [["G01", "G01 · Adquisición de mercancías"], ["G03", "G03 · Gastos en general"]];
const ENTREGAS = [["email", "Por correo"], ["descarga", "Descargar XML y PDF aquí"]];

// Campos de cada comercio: [id, etiqueta, tipo, opciones, de dónde sale en lo leído, confianza].
const CAMPOS = {
  walmart: [
    ["tc", "TC", "text", null, "tc_numero_ticket", "confianza_tc"],
    ["tr", "TR", "text", null, "tr_numero_transaccion", "confianza_tr"],
    ["forma_pago", "Forma de pago", "select", FORMAS_PAGO, "forma_pago_sat_sugerida", null],
    ["uso_cfdi", "Uso CFDI", "select", USOS_CFDI, null, null],
    ["metodo_entrega", "Entrega", "select", ENTREGAS, null, null],
  ],
  oxxo: [
    ["fecha", "Fecha", "date", null, "fecha", null],
    ["folio", "Folio", "text", null, "oxxo_folio_venta", null],
    ["id_venta", "ID de venta", "text", null, "oxxo_id_venta", "confianza_id_venta"],
    ["total", "Total", "number", null, "total", null],
    ["uso_cfdi", "Uso CFDI", "select", USOS_CFDI, null, null],
  ],
  heb: [
    ["sucursal", "Sucursal", "text", null, "heb_sucursal", null],
    ["ticket", "Ticket", "text", null, "heb_ticket", "confianza_tc"],
    ["fecha", "Fecha", "date", null, "fecha", null],
    ["total", "Total", "number", null, "total", null],
    ["uso_cfdi", "Uso CFDI", "select", USOS_CFDI, null, null],
  ],
};

const RUTAS = { walmart: "/facturas", oxxo: "/facturas/oxxo", heb: "/facturas/heb" };

function opciones(lista, elegido) {
  return lista.map(([valor, texto]) => el("option", { value: valor, selected: valor === elegido }, texto));
}

// Campos que no se deben suponer: si Gemini no los leyó, hay que elegirlos a mano.
const SIN_VALOR_POR_DEFECTO = ["forma_pago"];

function textoDeError(e) {
  // 422 de FastAPI: lista de {loc, msg}; se muestra campo por campo.
  if (Array.isArray(e.detalle)) return e.detalle.map((d) => `${d.loc[d.loc.length - 1]}: ${d.msg}`).join(" · ");
  return e.message;
}

export function montar(contenedor) {
  let leido = {};
  let urlVistaPrevia = null;
  let comercio = "";
  let lectura = 0;            // cada foto nueva invalida la lectura anterior que siga en curso

  // ---- foto
  const foto = el("input", { id: "foto", type: "file", accept: "image/*", clase: "solo-lector", tabindex: "-1",
    on: { change: () => procesar(foto.files[0]) } });
  const vacia = el("div", { clase: "zona-vacia" },
    icono("subir", "icono grande"),
    el("b", {}, "Arrastra la foto aquí"),
    el("span", {}, "o haz clic para elegirla · JPG, PNG o HEIC · hasta 10\u00a0MB"));
  const imagen = el("img", { alt: "Foto del ticket", clase: "foto-imagen" });
  const vistaFoto = el("div", { clase: "foto", hidden: true }, imagen, el("div", { clase: "lector" }));
  const estadoTexto = el("span", {}, "Leyendo con IA…");
  const cambiar = el("button", { type: "button", clase: "btn sec chico", on: { click: () => { limpiar(); abrir(); } } }, "Cambiar foto");
  const estadoFoto = el("div", { clase: "estado-foto", hidden: true, role: "status" },
    el("span", { clase: "estado-foto-texto" }, el("span", { clase: "punto" }), estadoTexto), cambiar);
  const zona = el("div", {
    clase: "zona", role: "button", tabindex: "0", "aria-label": "Subir imagen del ticket",
    on: {
      click: abrir,
      keydown: (e) => { if (e.target === zona && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); abrir(); } },
      dragenter: arrastrando, dragover: arrastrando,
      dragleave: () => zona.classList.remove("sobre"),
      drop: (e) => { e.preventDefault(); zona.classList.remove("sobre"); procesar(e.dataTransfer.files[0]); },
    },
  }, vacia, vistaFoto);
  const errorFoto = el("p", { clase: "ayuda error", role: "alert" });

  // ---- formulario
  const chips = Object.entries(COMERCIOS).map(([valor, texto]) => el("button", {
    type: "button", clase: "chip", "aria-pressed": "false", "data-comercio": valor,
    on: { click: () => elegirComercio(valor) },
  }, texto));
  const advertencias = el("div");
  const campos = el("div", { clase: "rejilla campos" });
  const error = el("div", { clase: "error", role: "alert" });
  const boton = el("button", { type: "submit", clase: "btn", disabled: true }, "Facturar");
  const formulario = el("form", { clase: "tarjeta formulario-ticket", novalidate: true, on: { submit: facturar } },
    el("div", {}, el("span", { clase: "label", id: "etiqueta-comercio" }, "Comercio"),
      el("div", { clase: "chips", role: "group", "aria-labelledby": "etiqueta-comercio" }, chips)),
    advertencias, campos, error,
    el("div", { clase: "acciones" }, boton, el("span", { clase: "ayuda" }, "Te pediremos confirmación antes del clic final.")));

  // ---- consulta
  const numero = el("input", { id: "numero-consulta", type: "text", clase: "mono", required: true, placeholder: "9906412345678901" });
  const errorConsulta = el("p", { clase: "ayuda error", role: "alert" });
  const consulta = el("form", { clase: "tarjeta consulta", on: { submit: consultar } },
    el("div", { clase: "campo" },
      el("label", { for: "numero-consulta" }, "¿Ya facturaste este ticket? Consulta con el TC o el folio"),
      numero, errorConsulta),
    el("button", { type: "submit", clase: "btn sec" }, "Consultar"));

  contenedor.append(
    el("h1", {}, "Nueva factura"),
    el("p", { clase: "subtitulo" }, "Sube la foto del ticket. La IA lo lee y tú revisas antes de facturar."),
    el("div", { clase: "nueva" },
      el("div", {}, el("label", { for: "foto", clase: "solo-lector" }, "Foto del ticket"), foto,
        // La barra de estado va fuera de la zona: un botón dentro de role="button" no se anuncia ni recibe Enter.
        el("div", { clase: "zona-contenedor" }, zona, estadoFoto), errorFoto),
      formulario),
    consulta);
  pintarCampos();

  function abrir() {
    foto.click();
  }

  function arrastrando(evento) {
    evento.preventDefault();
    zona.classList.add("sobre");
  }

  function limpiar() {
    lectura++;
    leido = {};
    foto.value = "";
    vacia.hidden = false;
    vistaFoto.hidden = true;
    estadoFoto.hidden = true;
    zona.classList.remove("leyendo");
    errorFoto.textContent = "";
    if (urlVistaPrevia) URL.revokeObjectURL(urlVistaPrevia);
    urlVistaPrevia = null;
  }

  async function procesar(archivo) {
    vaciar(error);
    errorFoto.textContent = "";
    if (!archivo) return;
    if (!archivo.type.startsWith("image/") || archivo.size > MAX_BYTES) {
      errorFoto.textContent = "Sube una imagen de hasta 10 MB.";
      foto.value = "";
      return;
    }
    if (urlVistaPrevia) URL.revokeObjectURL(urlVistaPrevia);
    urlVistaPrevia = URL.createObjectURL(archivo);
    imagen.src = urlVistaPrevia;
    vacia.hidden = true;
    vistaFoto.hidden = false;
    estadoFoto.hidden = false;
    estadoTexto.textContent = "Leyendo con IA…";
    zona.classList.add("leyendo");
    const mio = ++lectura;
    const datos = new FormData();
    datos.append("foto", archivo);
    let resultado;
    let fallo = null;
    try {
      resultado = await api("POST", "/tickets/extraer", datos);
    } catch (e) {
      fallo = e;
    }
    if (mio !== lectura) return;        // ya se eligió otra foto: esta lectura llegó tarde
    if (fallo) {
      leido = {};
      estadoTexto.textContent = "No se pudo leer";
      error.textContent = `${textoDeError(fallo)} Captura los datos a mano.`;
    } else {
      leido = resultado;
      estadoTexto.textContent = "Ticket leído";
    }
    zona.classList.remove("leyendo");
    elegirComercio(COMERCIOS[leido.comercio_clave] ? leido.comercio_clave : "");
    if (leido.comercio_clave === "otro") error.textContent = "Comercio no soportado; elige uno o captura a mano.";
    if (comercio) reproducir(campos, "llenar");
  }

  function elegirComercio(valor) {
    comercio = valor;
    chips.forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.comercio === valor)));
    pintarCampos();
  }

  function pintarCampos() {
    const lista = (leido.advertencias || []).map((a) => el("li", {}, a));
    vaciar(advertencias, lista.length
      ? el("div", { clase: "advertencia" }, icono("alerta"), el("ul", { clase: "advertencias" }, lista)) : null);
    const definicion = CAMPOS[comercio];
    boton.disabled = !definicion;
    if (!definicion) {
      vaciar(campos, el("p", { clase: "tenue campos-vacio" }, "Elige el comercio o sube la foto para ver los campos."));
      return;
    }
    vaciar(campos, definicion.map(([id, etiqueta, tipo, lista, origen, confianza]) => {
      const valor = origen && leido[origen] != null ? String(leido[origen]) : "";
      const sinLeer = SIN_VALOR_POR_DEFECTO.includes(id) && !valor;
      const revisar = sinLeer || (confianza && ["media", "baja"].includes(leido[confianza]));
      const mono = ["tc", "tr", "folio", "id_venta", "sucursal", "ticket", "total"].includes(id);
      const control = tipo === "select"
        ? el("select", { id: `campo-${id}`, name: id, required: true, clase: revisar ? "revisar" : undefined },
            sinLeer ? el("option", { value: "", selected: true }, "Elige…") : null,
            opciones(lista, sinLeer ? null : (valor || lista[0][0])))
        : el("input", { id: `campo-${id}`, name: id, type: tipo, value: valor, required: true,
            inputmode: mono && tipo === "text" ? "numeric" : undefined,
            step: tipo === "number" ? "0.01" : undefined,
            clase: [mono ? "mono" : "", revisar ? "revisar" : ""].join(" ").trim() || undefined });
      return el("div", { clase: "campo" }, el("label", { for: `campo-${id}` }, etiqueta), control);
    }));
  }

  async function facturar(evento) {
    evento.preventDefault();
    vaciar(error);
    const cuerpo = {};
    for (const [id, , tipoCampo] of CAMPOS[comercio]) {
      const control = campos.querySelector(`[name="${id}"]`);
      if (!control.value.trim()) {
        error.textContent = "Completa los campos marcados.";
        control.focus();
        return;
      }
      cuerpo[id] = tipoCampo === "number" ? Number(control.value) : control.value.trim();
    }
    boton.disabled = true;
    try {
      const t = await api("POST", RUTAS[comercio], cuerpo);
      location.hash = `#/trabajo/${encodeURIComponent(t.id)}`;
    } catch (e) {
      boton.disabled = false;
      error.textContent = textoDeError(e);
      if (e.status === 409 && e.detalle && e.detalle.trabajo_id) {
        const id = e.detalle.trabajo_id;
        error.append(" ", el("button", { type: "button", clase: "btn sec chico",
          on: { click: () => { location.hash = `#/trabajo/${encodeURIComponent(id)}`; } } }, "Ver trabajo existente"));
      }
    }
  }

  async function consultar(evento) {
    evento.preventDefault();
    errorConsulta.textContent = "";
    try {
      const t = await api("POST", "/consultas", { numero: numero.value.trim() });
      location.hash = `#/trabajo/${encodeURIComponent(t.id)}`;
    } catch (e) {
      errorConsulta.textContent = textoDeError(e);
    }
  }

  return () => { if (urlVistaPrevia) URL.revokeObjectURL(urlVistaPrevia); };
}
