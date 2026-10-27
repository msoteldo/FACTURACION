// Datos fiscales del receptor. Se guardan en la base y aplican a las facturas nuevas.
import { api } from "../api.js";
import { el, vaciar } from "../dom.js";

const CAMPOS = [
  ["rfc", "RFC"], ["razon_social", "Razón social"], ["cp", "CP"], ["calle", "Calle"],
  ["num_ext", "Núm. ext."], ["num_int", "Núm. int."], ["referencia", "Referencia"], ["estado", "Estado"],
  ["municipio", "Municipio"], ["colonia", "Colonia"], ["email", "Correo"], ["regimen_texto", "Régimen fiscal"],
];
const OPCIONALES = ["num_int", "referencia"];

export function montar(contenedor) {
  const aviso = el("div");
  const mensaje = el("p", { role: "status" });
  const controles = {};
  const errores = {};
  const rejilla = el("div", { clase: "rejilla" }, CAMPOS.map(([id, etiqueta]) => {
    controles[id] = el("input", { id: `perfil-${id}`, name: id, type: id === "email" ? "email" : "text",
      required: !OPCIONALES.includes(id) });
    errores[id] = el("p", { id: `error-${id}`, clase: "campo-error" });
    return el("div", {}, el("label", { for: `perfil-${id}` }, etiqueta), controles[id], errores[id]);
  }));
  const boton = el("button", { type: "submit" }, "Guardar");
  // noValidate: la validación la hace el servidor y sus errores se muestran junto a cada campo.
  const formulario = el("form", { clase: "tarjeta", noValidate: true, on: { submit: guardar } },
    el("h1", {}, "Datos fiscales"),
    el("p", { clase: "tenue" }, "Con estos datos se llenan los portales de los comercios."),
    aviso, rejilla, mensaje, boton);
  contenedor.append(formulario);
  cargar();

  function pintarOrigen(origen) {
    vaciar(aviso, origen === "entorno"
      ? el("p", { clase: "aviso" }, "Estos datos vienen de las variables de entorno. Guarda para poder editarlos aquí.")
      : null);
  }

  async function cargar() {
    try {
      const r = await api("GET", "/perfil");
      for (const [id] of CAMPOS) controles[id].value = r.datos[id] ?? "";
      pintarOrigen(r.origen);
    } catch (e) {
      mensaje.className = "error";
      mensaje.textContent = e.message;
    }
  }

  async function guardar(evento) {
    evento.preventDefault();
    for (const [id] of CAMPOS) errores[id].textContent = "";
    mensaje.textContent = "";
    const cuerpo = Object.fromEntries(CAMPOS.map(([id]) => [id, controles[id].value]));
    boton.disabled = true;
    try {
      const r = await api("PUT", "/perfil", cuerpo);
      pintarOrigen(r.origen);
      mensaje.className = "ok";
      mensaje.textContent = "Guardado. Aplica a las facturas nuevas; un trabajo en curso conserva sus datos.";
    } catch (e) {
      mensaje.className = "error";
      if (e.status === 422 && Array.isArray(e.detalle)) {
        for (const d of e.detalle) {
          const id = d.loc[d.loc.length - 1];
          if (errores[id]) errores[id].textContent = d.msg;
        }
        mensaje.textContent = "Revisa los campos marcados.";
      } else {
        mensaje.textContent = e.message;
      }
    } finally {
      boton.disabled = false;
    }
  }
}
