// Enrutador por hash: #/login, #/nueva, #/trabajo/<id>, #/perfil.
// Cada vista exporta montar(contenedor, parametro) y puede devolver una función para desmontarla
// (detener timers y consultas al cambiar de vista).
import { api } from "./api.js";
import { reproducir } from "./movimiento.js";
import * as login from "./vistas/login.js";
import * as nueva from "./vistas/nueva.js";
import * as perfil from "./vistas/perfil.js";
import * as trabajo from "./vistas/trabajo.js";

const VISTAS = { login, nueva, trabajo, perfil };
const contenedor = document.getElementById("vista");
const barra = document.getElementById("barra");
let desmontar = null;
let turno = 0;
let cuentaCargada = false;

function iniciales(nombre) {
  return nombre.split(/\s+/).filter((p) => /^[A-ZÁÉÍÓÚÑ]/i.test(p)).slice(0, 2).map((p) => p[0]).join("").toUpperCase();
}

// La razón social se pide una sola vez por carga de página (GET /perfil toca la base).
async function cargarCuenta() {
  if (cuentaCargada) return;
  cuentaCargada = true;
  try {
    const r = await api("GET", "/perfil");
    const nombre = (r.datos && r.datos.razon_social) || "";
    document.getElementById("cuenta-nombre").textContent = nombre;
    const avatar = document.getElementById("avatar");
    avatar.textContent = iniciales(nombre);
    avatar.hidden = !nombre;
  } catch {
    cuentaCargada = false;              // sin base o sin sesión: se reintenta en la siguiente carga
  }
}

function marcarPestana(nombre) {
  barra.querySelectorAll("[data-vista]").forEach((a) => {
    if (a.dataset.vista === nombre) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

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
  marcarPestana(nombre);
  if (nombre !== "login") {
    try {
      await api("GET", "/sesion");     // sin sesión, api() manda a #/login
    } catch {
      return;
    }
    if (mio !== turno) return;          // el usuario ya cambió de vista
    cargarCuenta();
  }
  desmontar = VISTAS[nombre].montar(contenedor, parametro ? decodeURIComponent(parametro) : undefined) || null;
  reproducir(contenedor, "entrando");
  // Lectores de pantalla: el foco va al título de la pantalla nueva (en el login, al campo).
  const titulo = contenedor.querySelector("h1");
  if (titulo && nombre !== "login") {
    titulo.tabIndex = -1;
    titulo.focus({ preventScroll: true });
  }
}

document.getElementById("salir").addEventListener("click", async () => {
  try { await api("DELETE", "/sesion"); } catch { /* la cookie se borra igual al vencer */ }
  location.hash = "#/login";
});

window.addEventListener("hashchange", navegar);
navegar();
