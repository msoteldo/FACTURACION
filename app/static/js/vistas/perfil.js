// Datos fiscales del receptor. Se guardan en la base y aplican a las facturas nuevas.
import { api } from "../api.js";
import { el, icono, vaciar } from "../dom.js";
import { avisar } from "../toast.js";

// [id, etiqueta, ayuda, opcional, monoespaciada, ancho completo]
const GRUPOS = [
  ["Contribuyente", [
    ["rfc", "RFC", "12 caracteres para empresas, 13 para personas.", false, true, false],
    ["regimen_texto", "Régimen fiscal", "Tal como aparece en el portal, p. ej. General de Ley Personas Morales.", false, false, false],
    ["razon_social", "Razón social", "Tal como aparece en tu constancia de situación fiscal.", false, false, true],
    ["email", "Correo para recibir facturas", "Los comercios mandan aquí el XML y el PDF.", false, false, false],
  ]],
  ["Domicilio fiscal", [
    ["cp", "Código postal (CP)", "5 dígitos.", false, true, false],
    ["colonia", "Colonia", "", false, false, false],
    ["calle", "Calle", "", false, false, false],
    ["num_ext", "Número exterior", "", false, false, false],
    ["num_int", "Número interior", "Opcional.", true, false, false],
    ["referencia", "Referencia", "Opcional.", true, false, false],
    ["municipio", "Municipio o alcaldía", "", false, false, false],
    ["estado", "Estado", "", false, false, false],
  ]],
];
// Los 422 de FastAPI vienen en inglés; se traducen por campo.
const MENSAJES = {
  rfc: "Debe tener 12 o 13 letras y números, sin espacios.",
  cp: "Debe tener 5 dígitos.",
  email: "Escribe un correo válido, p. ej. facturas@tuempresa.mx.",
};
const CAMPOS = GRUPOS.flatMap(([, campos]) => campos.map(([id]) => id));
const VOLVER_MS = 2400;

export function montar(contenedor) {
  const aviso = el("div");
  const controles = {};
  const errores = {};
  const grupos = GRUPOS.map(([titulo, campos]) => el("div", { clase: "tarjeta" }, el("fieldset", {},
    el("legend", {}, titulo),
    el("div", { clase: "rejilla" }, campos.map(([id, etiqueta, ayuda, opcional, mono, ancho]) => {
      controles[id] = el("input", { id: `perfil-${id}`, name: id, type: id === "email" ? "email" : "text",
        required: !opcional, clase: mono ? "mono" : undefined, inputmode: id === "cp" ? "numeric" : undefined,
        autocomplete: id === "email" ? "email" : id === "cp" ? "postal-code" : "off",
        "aria-describedby": `error-${id} ayuda-${id}` });
      errores[id] = el("p", { id: `error-${id}`, clase: "campo-error" });
      return el("div", { clase: ancho ? "campo ancho" : "campo" },
        el("label", { for: `perfil-${id}` }, etiqueta), controles[id], errores[id],
        ayuda ? el("p", { id: `ayuda-${id}`, clase: "ayuda" }, ayuda) : null);
    })))));
  const textoBoton = el("span", { clase: "boton-texto" }, "Guardar");
  const boton = el("button", { type: "submit", clase: "btn guardar" }, textoBoton);
  const estado = el("span", { clase: "ayuda", role: "status" });
  const formulario = el("form", { clase: "datos", novalidate: true, on: { submit: guardar } },
    aviso, grupos, el("div", { clase: "acciones" }, boton, estado));
  contenedor.append(
    el("h1", {}, "Datos fiscales"),
    el("p", { clase: "subtitulo" }, "Con estos datos se llena el portal de cada comercio. Aplican a las facturas nuevas."),
    formulario);
  cargar();

  function pintarOrigen(origen) {
    vaciar(aviso, origen === "entorno"
      ? el("div", { clase: "aviso" }, icono("info"),
          el("span", {}, "Estos datos vienen de la configuración del servidor. Guarda para poder editarlos aquí."))
      : null);
  }

  async function cargar() {
    try {
      const r = await api("GET", "/perfil");
      for (const id of CAMPOS) controles[id].value = r.datos[id] ?? "";
      pintarOrigen(r.origen);
    } catch (e) {
      if (e.status !== 401) avisar(e.message, "error");
    }
  }

  function cambiarTexto(texto) {
    // El cambio de texto del botón se suaviza con un desenfoque breve (solo opacity y filter).
    textoBoton.classList.add("cambiando");
    setTimeout(() => {
      textoBoton.textContent = texto;
      textoBoton.classList.remove("cambiando");
    }, 120);
  }

  async function guardar(evento) {
    evento.preventDefault();
    for (const id of CAMPOS) errores[id].textContent = "";
    estado.textContent = "";
    const cuerpo = Object.fromEntries(CAMPOS.map((id) => [id, controles[id].value]));
    boton.disabled = true;
    try {
      const r = await api("PUT", "/perfil", cuerpo);
      pintarOrigen(r.origen);
      cambiarTexto("Guardado ✓");
      setTimeout(() => cambiarTexto("Guardar"), VOLVER_MS);
      avisar("Datos guardados. Aplican a las facturas nuevas.");
    } catch (e) {
      if (e.status === 422 && Array.isArray(e.detalle)) {
        let primero = null;
        for (const d of e.detalle) {
          const id = d.loc[d.loc.length - 1];
          if (errores[id]) {
            errores[id].textContent = MENSAJES[id] || "Este campo es obligatorio.";
            primero = primero || controles[id];
          }
        }
        estado.textContent = "Revisa los campos marcados.";
        if (primero) primero.focus();
      } else if (e.status !== 401) {
        avisar(e.message, "error");
      }
    } finally {
      boton.disabled = false;
    }
  }
}
