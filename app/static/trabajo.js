import { el } from "./dom.js";

export function montar(contenedor) {
  contenedor.append(el("h1", {}, "trabajo"));
}
