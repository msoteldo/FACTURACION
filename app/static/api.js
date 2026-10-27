// Llamadas a la API con la cookie de sesión. X-Requested-With es obligatorio para los métodos
// que cambian algo (barrera anti-CSRF del servidor).
export class ErrorApi extends Error {
  constructor(status, detalle) {
    const texto = typeof detalle === "string" ? detalle
      : (detalle && typeof detalle.error === "string") ? detalle.error
      : `Error ${status}`;
    super(texto);
    this.status = status;
    this.detalle = detalle;
  }
}

let pendientes = 0;

function guardarVolver() {
  try {
    if (!location.hash.startsWith("#/login")) sessionStorage.setItem("volver", location.hash);
  } catch { /* sin sessionStorage: se vuelve a #/nueva */ }
}

export async function api(metodo, ruta, cuerpo) {
  const opciones = { method: metodo, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } };
  if (cuerpo instanceof FormData) opciones.body = cuerpo;
  else if (cuerpo !== undefined) {
    opciones.headers["Content-Type"] = "application/json";
    opciones.body = JSON.stringify(cuerpo);
  }
  // Render gratis se duerme: si tarda, se avisa en vez de parecer colgado.
  const aviso = document.getElementById("aviso-servidor");
  pendientes++;
  const temporizador = setTimeout(() => { aviso.hidden = false; }, 3000);
  let r;
  try {
    r = await fetch(ruta, opciones);
  } catch {
    throw new ErrorApi(0, "Sin conexión con el servidor.");
  } finally {
    clearTimeout(temporizador);
    if (--pendientes === 0) aviso.hidden = true;
  }
  let cuerpoRespuesta = null;
  if (r.status !== 204) {
    const tipo = r.headers.get("content-type") || "";
    cuerpoRespuesta = tipo.includes("application/json") ? await r.json() : await r.text();
  }
  // Un 401 del propio login es "contraseña incorrecta", no "sesión vencida".
  if (r.status === 401 && !(metodo === "POST" && ruta === "/sesion")) {
    guardarVolver();
    location.hash = "#/login";
  }
  if (!r.ok) throw new ErrorApi(r.status, cuerpoRespuesta && cuerpoRespuesta.detail !== undefined
    ? cuerpoRespuesta.detail : cuerpoRespuesta);
  return cuerpoRespuesta;
}
