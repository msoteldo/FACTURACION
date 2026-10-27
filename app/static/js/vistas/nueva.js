// Nueva factura: foto -> datos leídos por Gemini -> formulario editable -> crear trabajo.
import { api } from "../api.js";
import { el, vaciar } from "../dom.js";

const MAX_BYTES = 10 * 1024 * 1024;

const COMERCIOS = {
  walmart: "Sam's Club / Walmart",
  oxxo: "OXXO",
  heb: "HEB",
};

const FORMAS_PAGO = [["01", "Efectivo"], ["04", "Tarjeta de crédito"], ["28", "Tarjeta de débito"],
  ["05", "Monedero electrónico"]];
const USOS_CFDI = [["G01", "G01 · Adquisición de mercancías"], ["G03", "G03 · Gastos en general"]];
const ENTREGAS = [["email", "Por correo"], ["descarga", "Descargar XML/PDF aquí"]];

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

  const foto = el("input", { id: "foto", type: "file", accept: "image/*", on: { change: subirFoto } });
  const vistaPrevia = el("div");
  const estadoFoto = el("p", { clase: "tenue", role: "status" });
  const comercio = el("select", { id: "comercio", on: { change: () => pintarCampos() } },
    el("option", { value: "" }, "Elige el comercio…"),
    Object.entries(COMERCIOS).map(([valor, texto]) => el("option", { value: valor }, texto)));
  const advertencias = el("div");
  const campos = el("div", { clase: "rejilla" });
  const error = el("div", { clase: "error", role: "alert" });
  const boton = el("button", { type: "submit" }, "Facturar");
  const formulario = el("form", { clase: "tarjeta", on: { submit: facturar } },
    el("h1", {}, "Nueva factura"),
    el("label", { for: "foto" }, "Foto del ticket"), foto, estadoFoto, vistaPrevia,
    el("label", { for: "comercio" }, "Comercio"), comercio,
    advertencias, campos, error, boton);

  const numero = el("input", { id: "numero-consulta", type: "text", required: true });
  const errorConsulta = el("p", { clase: "error", role: "alert" });
  const consulta = el("form", { clase: "tarjeta", on: { submit: consultar } },
    el("h2", {}, "Consultar factura ya emitida (Sam's Club / Walmart)"),
    el("label", { for: "numero-consulta" }, "TC o folio"), numero, errorConsulta,
    el("button", { type: "submit", clase: "secundario" }, "Consultar"));

  contenedor.append(formulario, consulta);
  pintarCampos();

  async function subirFoto() {
    const archivo = foto.files[0];
    vaciar(error);
    if (!archivo) return;
    if (!archivo.type.startsWith("image/") || archivo.size > MAX_BYTES) {
      error.textContent = "Sube una imagen de hasta 10 MB.";
      foto.value = "";
      return;
    }
    if (urlVistaPrevia) URL.revokeObjectURL(urlVistaPrevia);
    urlVistaPrevia = URL.createObjectURL(archivo);
    vaciar(vistaPrevia, el("img", { src: urlVistaPrevia, alt: "Ticket", clase: "vista-previa" }));
    estadoFoto.textContent = "Leyendo el ticket…";
    const datos = new FormData();
    datos.append("foto", archivo);
    try {
      leido = await api("POST", "/tickets/extraer", datos);
      estadoFoto.textContent = "Ticket leído. Revisa los datos antes de facturar.";
    } catch (e) {
      leido = {};
      estadoFoto.textContent = "";
      error.textContent = `${textoDeError(e)} Captura los datos a mano.`;
    }
    comercio.value = COMERCIOS[leido.comercio_clave] ? leido.comercio_clave : "";
    if (leido.comercio_clave === "otro") error.textContent = "Comercio no soportado; elige uno o captura a mano.";
    pintarCampos();
  }

  function pintarCampos() {
    const lista = (leido.advertencias || []).map((a) => el("li", {}, a));
    vaciar(advertencias, lista.length ? el("ul", { clase: "advertencias aviso" }, lista) : null);
    const definicion = CAMPOS[comercio.value];
    boton.disabled = !definicion;
    if (!definicion) {
      vaciar(campos);
      return;
    }
    vaciar(campos, definicion.map(([id, etiqueta, tipo, lista, origen, confianza]) => {
      const valor = origen && leido[origen] != null ? String(leido[origen]) : "";
      const sinLeer = SIN_VALOR_POR_DEFECTO.includes(id) && !valor;
      const revisar = sinLeer || (confianza && ["media", "baja"].includes(leido[confianza]));
      const control = tipo === "select"
        ? el("select", { id: `campo-${id}`, name: id, required: true, clase: revisar ? "revisar" : undefined },
            sinLeer ? el("option", { value: "", selected: true }, "Elige…") : null,
            opciones(lista, sinLeer ? null : (valor || lista[0][0])))
        : el("input", { id: `campo-${id}`, name: id, type: tipo, value: valor, required: true,
            step: tipo === "number" ? "0.01" : undefined, clase: revisar ? "revisar" : undefined });
      return el("div", {}, el("label", { for: `campo-${id}` }, etiqueta), control);
    }));
  }

  async function facturar(evento) {
    evento.preventDefault();
    vaciar(error);
    const tipo = comercio.value;
    const cuerpo = {};
    for (const [id, , tipoCampo] of CAMPOS[tipo]) {
      const valor = campos.querySelector(`[name="${id}"]`).value.trim();
      cuerpo[id] = tipoCampo === "number" ? Number(valor) : valor;
    }
    boton.disabled = true;
    try {
      const t = await api("POST", RUTAS[tipo], cuerpo);
      location.hash = `#/trabajo/${encodeURIComponent(t.id)}`;
    } catch (e) {
      boton.disabled = false;
      error.textContent = textoDeError(e);
      if (e.status === 409 && e.detalle && e.detalle.trabajo_id) {
        const id = e.detalle.trabajo_id;
        error.append(" ", el("button", { type: "button", clase: "secundario",
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
