// Entrar: el formulario es un ticket térmico que sale de la ranura de una impresora.
import { api } from "../api.js";
import { el } from "../dom.js";
import { reproducir } from "../movimiento.js";

export function montar(contenedor) {
  const campo = el("input", { id: "password", type: "password", autocomplete: "current-password", required: true });
  const error = el("p", { clase: "ayuda error", role: "alert", id: "password-error" });
  const boleto = el("form", { clase: "boleto ticket-termico", on: { submit: entrar }, "aria-labelledby": "titulo-login" },
    el("div", { clase: "boleto-encabezado" },
      el("span", { clase: "marca" }, logo(), el("span", {}, "Ticket", el("span", { clase: "ia" }, "IA"))),
      el("small", {}, "ACCESO · FACTURACIÓN CFDI 4.0")),
    el("hr", { clase: "corte" }),
    el("h1", { id: "titulo-login" }, "Entra a tu cuenta"),
    el("div", { clase: "campo" }, el("label", { for: "password" }, "Contraseña"), campo, error),
    el("button", { type: "submit", clase: "btn" }, "Entrar"),
    el("hr", { clase: "corte" }),
    el("div", { clase: "renglon" }, el("span", {}, "SESIÓN"), el("span", {}, "12 H")),
    el("div", { clase: "renglon" }, el("span", {}, "CONEXIÓN"), el("span", {}, "CIFRADA")));
  const clip = el("div", { clase: "boleto-clip" }, boleto);
  contenedor.append(el("div", { clase: "login" },
    el("div", { clase: "ranura" }, clip),
    el("p", { clase: "debajo" }, "¿Primera vez aquí? ", el("a", { href: "/" }, "Conoce TicketIA"))));
  campo.setAttribute("aria-describedby", "password-error");
  campo.focus({ preventScroll: true });

  async function entrar(evento) {
    evento.preventDefault();
    error.textContent = "";
    try {
      await api("POST", "/sesion", { password: campo.value });
    } catch (e) {
      error.textContent = e.status === 401 ? "Contraseña incorrecta." : e.message;
      reproducir(clip, "temblar");
      campo.select();
      return;
    }
    let volver = null;
    try {
      volver = sessionStorage.getItem("volver");
      sessionStorage.removeItem("volver");
    } catch { /* sin sessionStorage */ }
    location.hash = volver || "#/nueva";
  }
}

function logo() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "logo");
  svg.setAttribute("aria-hidden", "true");
  const uso = document.createElementNS("http://www.w3.org/2000/svg", "use");
  uso.setAttribute("href", "#i-logo");
  svg.append(uso);
  return svg;
}
