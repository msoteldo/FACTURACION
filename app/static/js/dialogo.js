// Confirmación dentro de la página (sustituye a window.confirm): foco atrapado, Escape cancela
// y el foco vuelve a donde estaba. Solo hay un diálogo abierto a la vez.
import { el } from "./dom.js";
import { reducido } from "./movimiento.js";

let abierto = null;

export function confirmar({ titulo, texto, aceptar = "Aceptar", cancelar = "Cancelar" }) {
  if (abierto) abierto.cerrar(false);
  const previo = document.activeElement;
  return new Promise((resolver) => {
    let resuelto = false;
    const idTitulo = `dialogo-${Date.now()}`;
    const botonCancelar = el("button", { type: "button", clase: "btn sec", on: { click: () => cerrar(false) } }, cancelar);
    const botonAceptar = el("button", { type: "button", clase: "btn", on: { click: () => cerrar(true) } }, aceptar);
    const dialogo = el("div", { clase: "dialogo", role: "dialog", "aria-modal": "true", "aria-labelledby": idTitulo },
      el("h2", { id: idTitulo }, titulo),
      el("p", {}, texto),
      el("div", { clase: "dialogo-acciones" }, botonCancelar, botonAceptar));
    const capa = el("div", { clase: "capa", on: { keydown: teclas, mousedown: (e) => { if (e.target === capa) cerrar(false); } } }, dialogo);
    document.body.append(capa);
    botonAceptar.focus();

    function teclas(evento) {
      if (evento.key === "Escape") {
        evento.preventDefault();
        cerrar(false);
      } else if (evento.key === "Tab") {
        const orden = [botonCancelar, botonAceptar];
        const i = orden.indexOf(document.activeElement);
        evento.preventDefault();
        orden[(i + (evento.shiftKey ? -1 : 1) + orden.length) % orden.length].focus();
      }
    }

    function cerrar(valor) {
      if (resuelto) return;           // un doble clic no responde dos veces
      resuelto = true;
      abierto = null;
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
