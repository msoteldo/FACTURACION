// Confirmación dentro de la página (sustituye a window.confirm): foco atrapado, Escape cancela
// y el foco vuelve a donde estaba. Solo hay un diálogo abierto a la vez.
import { el } from "./dom.js";
import { reducido } from "./movimiento.js";

// Un doble clic en el botón que abre el diálogo no debe cerrarlo con su segundo clic.
const GRACIA_MS = 400;
let abierto = null;

export function confirmar({ titulo, texto, aceptar = "Aceptar", cancelar = "Cancelar" }) {
  if (abierto) abierto.cerrar(false);
  const previo = document.activeElement;
  return new Promise((resolver) => {
    let resuelto = false;
    let pulsadoEnCapa = false;
    const abiertoEn = Date.now();
    const idTitulo = `dialogo-${abiertoEn}`;
    const botonCancelar = el("button", { type: "button", clase: "btn sec", on: { click: () => cerrar(false) } }, cancelar);
    const botonAceptar = el("button", { type: "button", clase: "btn", on: { click: () => cerrar(true) } }, aceptar);
    // tabindex -1: un clic en el título o el texto deja el foco dentro del diálogo.
    const dialogo = el("div", { clase: "dialogo", role: "dialog", "aria-modal": "true", "aria-labelledby": idTitulo, tabindex: "-1" },
      el("h2", { id: idTitulo }, titulo),
      el("p", {}, texto),
      el("div", { clase: "dialogo-acciones" }, botonCancelar, botonAceptar));
    const capa = el("div", {
      clase: "capa",
      on: {
        mousedown: (e) => { pulsadoEnCapa = e.target === capa; },
        click: (e) => {
          if (e.target === capa && pulsadoEnCapa && Date.now() - abiertoEn > GRACIA_MS) cerrar(false);
        },
      },
    }, dialogo);

    // Lo que queda detrás no recibe foco ni clics mientras el diálogo esté abierto.
    const fondo = [...document.body.children].filter((n) => !n.inert && n.tagName !== "SCRIPT");
    fondo.forEach((n) => { n.inert = true; });
    document.body.append(capa);
    document.addEventListener("keydown", teclas, true);
    botonAceptar.focus();

    function teclas(evento) {
      if (evento.key === "Escape") {
        evento.preventDefault();
        cerrar(false);
      } else if (evento.key === "Tab") {
        const orden = [botonCancelar, botonAceptar];
        const i = orden.indexOf(document.activeElement);
        evento.preventDefault();
        const siguiente = i === -1 ? (evento.shiftKey ? orden.length - 1 : 0)
          : (i + (evento.shiftKey ? -1 : 1) + orden.length) % orden.length;
        orden[siguiente].focus();
      }
    }

    function cerrar(valor) {
      if (resuelto) return;           // un doble clic no responde dos veces
      resuelto = true;
      abierto = null;
      document.removeEventListener("keydown", teclas, true);
      fondo.forEach((n) => { n.inert = false; });
      capa.classList.add("saliendo");
      setTimeout(() => capa.remove(), reducido() ? 0 : 150);
      if (previo && previo.isConnected) previo.focus();
      resolver(valor);
    }

    abierto = { cerrar };
  });
}

export function cerrarDialogo() {
  if (abierto) abierto.cerrar(false);
}
