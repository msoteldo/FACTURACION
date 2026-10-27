// Enrutador por hash: #/login, #/nueva, #/trabajo/<id>, #/perfil.
// Cada vista exporta montar(contenedor, parametro) y puede devolver una función para desmontarla
// (detener timers y consultas al cambiar de vista).
import { api } from "./api.js";
import * as login from "./login.js";
import * as nueva from "./nueva.js";
import * as perfil from "./perfil.js";
import * as trabajo from "./trabajo.js";

const VISTAS = { login, nueva, trabajo, perfil };
const contenedor = document.getElementById("vista");
const barra = document.getElementById("barra");
let desmontar = null;
let turno = 0;

async function navegar() {
  const [nombre, parametro] = location.hash.replace(/^#\/?/, "").split("/");
  if (!VISTAS[nombre]) {
    location.hash = "#/nueva";
    return;
  }
  const mio = ++turno;
  if (desmontar) desmontar();
  desmontar = null;
  contenedor.replaceChildren();
  barra.hidden = nombre === "login";
  if (nombre !== "login") {
    try {
      await api("GET", "/sesion");     // sin sesión, api() manda a #/login
    } catch {
      return;
    }
    if (mio !== turno) return;          // el usuario ya cambió de vista
  }
  desmontar = VISTAS[nombre].montar(contenedor, parametro ? decodeURIComponent(parametro) : undefined) || null;
}

document.getElementById("salir").addEventListener("click", async () => {
  try { await api("DELETE", "/sesion"); } catch { /* la cookie se borra igual al vencer */ }
  location.hash = "#/login";
});

window.addEventListener("hashchange", navegar);
navegar();
