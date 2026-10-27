// Utilidades de movimiento. Curvas y duraciones viven en css/tokens.css; aquí solo se disparan.

export function reducido() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Vuelve a reproducir la animación CSS ligada a una clase (quita, fuerza reflow, pone).
export function reproducir(elemento, clase) {
  elemento.classList.remove(clase);
  void elemento.offsetWidth;
  elemento.classList.add(clase);
}
