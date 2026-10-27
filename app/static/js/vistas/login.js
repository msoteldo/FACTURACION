import { api } from "../api.js";
import { el } from "../dom.js";

export function montar(contenedor) {
  const campo = el("input", { id: "password", type: "password", autocomplete: "current-password", required: true });
  const error = el("p", { clase: "error", role: "alert" });
  const formulario = el("form", { clase: "tarjeta angosta", on: { submit: entrar } },
    el("h1", {}, "Entrar"),
    el("label", { for: "password" }, "Contraseña"),
    campo,
    error,
    el("button", { type: "submit" }, "Entrar"));
  contenedor.append(formulario);
  campo.focus();

  async function entrar(evento) {
    evento.preventDefault();
    error.textContent = "";
    try {
      await api("POST", "/sesion", { password: campo.value });
    } catch (e) {
      error.textContent = e.status === 401 ? "Contraseña incorrecta." : e.message;
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
