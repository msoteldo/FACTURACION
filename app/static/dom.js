// Crea elementos sin HTML crudo: los textos siempre entran como nodos de texto (nunca innerHTML),
// así un texto que viene del portal o de Gemini no puede inyectar etiquetas ni scripts.
export function el(tag, props = {}, ...hijos) {
  const nodo = document.createElement(tag);
  for (const [clave, valor] of Object.entries(props)) {
    if (valor === undefined || valor === null || valor === false) continue;
    if (clave === "clase") nodo.className = valor;
    else if (clave === "on") for (const [evento, fn] of Object.entries(valor)) nodo.addEventListener(evento, fn);
    else if (clave in nodo && typeof nodo[clave] !== "string") nodo[clave] = valor;
    else nodo.setAttribute(clave, valor === true ? "" : valor);
  }
  for (const hijo of hijos.flat()) {
    if (hijo === undefined || hijo === null || hijo === false) continue;
    nodo.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return nodo;
}

export function vaciar(nodo, ...hijos) {
  nodo.replaceChildren(...hijos.flat().filter(Boolean));
  return nodo;
}
