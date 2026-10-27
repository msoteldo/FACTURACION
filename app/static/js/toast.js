// Avisos breves abajo a la derecha. Entran y salen por abajo; el tiempo se pausa con la pestaña oculta.
import { el, icono } from "./dom.js";

const DURACION_MS = 3200;
let contenedor = null;

export function avisar(texto, tipo = "ok") {
  if (!contenedor) {
    contenedor = el("div", { clase: "toasts", "aria-live": "polite" });
    document.body.append(contenedor);
  }
  const aviso = el("div", { clase: `toast ${tipo}`, role: tipo === "error" ? "alert" : undefined },
    icono(tipo === "error" ? "alerta" : "check"), el("span", {}, texto));
  contenedor.append(aviso);

  let restante = DURACION_MS;
  let inicio = Date.now();
  let temporizador = setTimeout(salir, restante);

  function pausar() {
    if (document.hidden) {
      clearTimeout(temporizador);
      restante -= Date.now() - inicio;
    } else {
      inicio = Date.now();
      temporizador = setTimeout(salir, restante);
    }
  }
  document.addEventListener("visibilitychange", pausar);

  function salir() {
    document.removeEventListener("visibilitychange", pausar);
    aviso.classList.add("saliendo");
    setTimeout(() => aviso.remove(), 180);
  }
}
