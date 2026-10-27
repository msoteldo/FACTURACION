"""Automatización del portal de facturación de OXXO (PrimeFaces, una sola página).

Pantalla (inicio.do):
  0. (Opcional) Inicio de sesión de "usuario frecuente": form:usuario / form:pass / Entrar.
  1. Datos del ticket: fecha (calendario de solo lectura), folio, ID de venta y total,
     y el enlace "Validar Ticket" (solo valida, no factura).
  2. Datos de facturación: "Selecciona tu RFC" (si hay sesión), RFC, domicilio, régimen y
     uso CFDI, y los botones "Continuar" / "Generar Factura".

OJO: con sesión iniciada, el clic en "Continuar" generó la factura directamente (sin la
ventana "Verifica tus datos"). Por eso la confirmación humana va ANTES de "Continuar" y,
desde ese clic, el trabajo se marca como facturado.
"""
import time
import unicodedata
from dataclasses import dataclass
from datetime import date

from playwright.sync_api import TimeoutError as PWTimeout

from .portal_walmart import FlujoCancelado, FlujoError, FlujoWalmart

USO_CFDI_TEXTO = {"G01": "adquisici", "G03": "gastos en general"}

# Campos de la sección 2 (ids) -> atributo de DatosFiscales.
CAMPOS_FISCALES = {
    "form:rfc": "rfc", "form:razon": "razon_social", "form:calle": "calle", "form:ext": "num_ext",
    "form:int": "num_int", "form:colonia": "colonia", "form:dele": "municipio", "form:codigo": "cp",
}
ETIQUETAS_FISCALES = {
    "form:rfc": "RFC", "form:razon": "Razón social", "form:calle": "Calle", "form:ext": "Núm. exterior",
    "form:int": "Núm. interior", "form:colonia": "Colonia", "form:dele": "Municipio",
    "form:codigo": "Código postal",
}


def _norm(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower().strip()


def _sel(id_):
    """Selector CSS para ids de JSF con ':' (form:folio -> [id='form:folio'])."""
    return f"[id='{id_}']"


@dataclass
class SolicitudOxxo:
    fecha: date
    folio: str          # Fol_Vta (hasta 9 dígitos)
    id_venta: str       # ID= (hasta 12 letras/dígitos)
    total: float
    uso_cfdi: str       # "G01" | "G03"
    metodo_entrega: str = "email"  # OXXO la envía al correo de la cuenta

    @property
    def tc(self):
        """Llave para evitar dos trabajos simultáneos del mismo ticket."""
        return f"OXXO-{self.folio}-{self.id_venta}"


class FlujoOxxo(FlujoWalmart):
    def __init__(self, page, datos, interaccion, usuario="", password=""):
        super().__init__(page, datos, interaccion)
        self.usuario = usuario
        self.password = password

    # ---------------------------------------------------------------- utilidades PrimeFaces

    def esperar_ajax(self, ms=1500, timeout_ms=60000):
        """Espera a que termine la petición AJAX de PrimeFaces (barra "Espere por favor...")."""
        try:
            self.page.wait_for_function(
                "() => !window.PrimeFaces || !PrimeFaces.ajax || !PrimeFaces.ajax.Queue"
                " || PrimeFaces.ajax.Queue.isEmpty()", timeout=timeout_ms)
        except PWTimeout:
            pass
        self.page.wait_for_timeout(ms)

    def habilitado(self, id_):
        loc = self.page.locator(_sel(id_))
        if not loc.count():
            return False
        return loc.first.evaluate(
            "e => !e.disabled && !e.classList.contains('ui-state-disabled')")

    def escribir(self, id_, valor):
        loc = self.page.locator(_sel(id_))
        if not loc.count() or not self.habilitado(id_):
            return False
        loc.first.fill(str(valor))
        loc.first.dispatch_event("change")
        loc.first.dispatch_event("blur")
        return True

    def pf_select(self, base_id, contiene=None, valor=None):
        """Elige una opción de un selectOneMenu de PrimeFaces (select oculto + widget).
        Usa el widget si existe (dispara el AJAX del portal); si no, el <select> nativo."""
        resultado = self.page.evaluate(
            """([base, contiene, valor]) => {
                const sel = document.getElementById(base + '_input');
                if (!sel || sel.disabled) return null;
                const norm = s => s.normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
                const op = Array.from(sel.options).find(o => o.value && (
                    valor ? o.value === valor : norm(o.text).includes(norm(contiene))));
                if (!op) return {opciones: Array.from(sel.options).map(o => o.text)};
                let usado = 'nativo';
                if (window.PrimeFaces && PrimeFaces.widgets) {
                    for (const k in PrimeFaces.widgets) {
                        const w = PrimeFaces.widgets[k];
                        if (w && w.id === base && typeof w.selectValue === 'function') {
                            w.selectValue(op.value); usado = 'widget'; break;
                        }
                    }
                }
                if (usado === 'nativo') {
                    sel.value = op.value;
                    if (window.jQuery) jQuery(sel).trigger('change');
                    else sel.dispatchEvent(new Event('change', {bubbles: true}));
                    const label = document.getElementById(base + '_label');
                    if (label) label.textContent = op.text;
                }
                return {texto: op.text, usado};
            }""", [base_id, contiene, valor])
        return resultado

    def texto_select(self, base_id):
        return self.page.evaluate(
            """b => { const s = document.getElementById(b + '_input');
                     return s && s.selectedIndex >= 0 && s.value ? s.options[s.selectedIndex].text.trim() : ''; }""",
            base_id)

    def dialogo_visible(self, texto):
        """¿Hay un diálogo visible que contenga `texto`?"""
        return self.page.evaluate(
            """t => Array.from(document.querySelectorAll('[role=dialog], .ui-dialog')).some(d => {
                    const visible = d.offsetWidth > 0 && d.offsetHeight > 0
                                    && getComputedStyle(d).visibility !== 'hidden';
                    return visible && (d.innerText || '').toLowerCase().includes(t.toLowerCase());
                })""", texto)

    def clic_en_dialogo(self, texto_dialogo, texto_boton):
        dialogo = self.page.locator("[role=dialog], .ui-dialog").filter(has_text=texto_dialogo)
        boton = dialogo.locator("button").filter(has_text=texto_boton).locator("visible=true")
        if boton.count():
            boton.first.click()
            self.esperar_ajax()
            return True
        return False

    def mensajes_growl(self):
        return self.page.evaluate(
            """() => Array.from(document.querySelectorAll('.ui-growl-message, .ui-messages-error, .ui-message-error'))
                     .filter(e => e.offsetWidth > 0).map(e => e.innerText.trim()).filter(Boolean)""")

    def cerrar_aviso_inactividad(self):
        if self.dialogo_visible("tiempo de inactividad"):
            self.clic_en_dialogo("tiempo de inactividad", "Aceptar")

    # ---------------------------------------------------------------- pasos

    def abrir_oxxo(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.esperar_ajax(1500)
        self.cerrar_aviso_inactividad()

    def con_sesion(self):
        return bool(self.page.locator(_sel("form:rfcs_input")).count()) or \
            "tu listado" in self.texto_visible(5000).lower()

    def iniciar_sesion(self):
        if not self.usuario or self.con_sesion():
            return
        if not self.page.locator(_sel("form:usuario")).count():
            self.ui.evento("No encontré el formulario de inicio de sesión; sigo sin sesión.")
            return
        self.page.locator(_sel("form:usuario")).first.fill(self.usuario)
        self.page.locator(_sel("form:pass")).first.fill(self.password)
        entrar = self.page.locator("[id='form'] button").filter(has_text="Entrar")
        if not entrar.count():
            self.fallar("No encontré el botón 'Entrar' del inicio de sesión.", "01_login_error")
        entrar.first.click()
        self.esperar_ajax(2500)
        self.cerrar_aviso_inactividad()
        if not self.con_sesion():
            msgs = self.mensajes_growl()
            self.fallar("No se pudo iniciar sesión en OXXO" + (f": {' '.join(msgs)}" if msgs else
                        " (revisa OXXO_USUARIO / OXXO_PASSWORD)."), "01_login_error")
        self.ui.evento("Sesión iniciada en el portal de OXXO.")

    def poner_fecha(self, fecha):
        """El calendario es de solo lectura: se usa el widget de PrimeFaces (o se escribe el
        valor con el formato del widget, por defecto dd/MM/yyyy)."""
        r = self.page.evaluate(
            """([y, m, d]) => {
                const inp = document.getElementById('form:fecha_input');
                if (!inp) return null;
                const fecha = new Date(y, m - 1, d);
                if (window.PrimeFaces && PrimeFaces.widgets) {
                    for (const k in PrimeFaces.widgets) {
                        const w = PrimeFaces.widgets[k];
                        if (w && w.id === 'form:fecha' && typeof w.setDate === 'function') {
                            w.setDate(fecha);
                            if (window.jQuery) jQuery(inp).trigger('change');
                            return inp.value;
                        }
                    }
                }
                const dos = n => String(n).padStart(2, '0');
                inp.removeAttribute('readonly');
                inp.value = dos(d) + '/' + dos(m) + '/' + y;
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                return inp.value;
            }""", [fecha.year, fecha.month, fecha.day])
        if not r:
            self.fallar("No pude capturar la fecha de venta.", "02_fecha_error")
        return r

    def pantalla_ticket_oxxo(self, sol):
        fecha_portal = self.poner_fecha(sol.fecha)
        faltan = [n for n, v in (("form:folio", sol.folio), ("form:venta", sol.id_venta),
                                 ("form:total", f"{sol.total:.2f}")) if not self.escribir(n, v)]
        if faltan:
            self.fallar(f"No pude llenar {faltan} en los datos del ticket.", "02_ticket_error")
        self.resumen.update({"fecha_portal": fecha_portal})
        self.ui.captura(self.page, "02_ticket_llenado")
        self.revisar_captcha("ticket")

        validar = self.page.locator(_sel("form:validarTicket"))
        if not validar.count():
            self.fallar("No encontré 'Validar Ticket'.", "03_validar_error")
        validar.first.click()  # solo valida; no factura
        self.esperar_ajax(2000)
        self.revisar_resultado_validacion()

    def revisar_resultado_validacion(self):
        fin = time.time() + 20
        while time.time() < fin:
            if self.dialogo_visible("facturado en tienda"):
                self.ui.captura(self.page, "03_facturado_en_tienda")
                self.clic_en_dialogo("facturado en tienda", "Cancelar")
                raise FlujoError("OXXO dice que este ticket ya fue facturado en tienda. "
                                 "Puedes descargar el XML en el portal de consultas de OXXO.")
            if self.dialogo_visible("ticket no encontrado"):
                self.ui.captura(self.page, "03_ticket_no_encontrado")
                raise FlujoError("OXXO no encontró el ticket. Revisa fecha, folio, ID de venta y "
                                 "total; si la compra es muy reciente, intenta más tarde.")
            if self.habilitado("form:continuar"):
                self.ui.evento("Ticket validado por OXXO.")
                return
            msgs = self.mensajes_growl()
            if msgs:
                self.fallar(f"OXXO rechazó el ticket: {' '.join(msgs)}", "03_validacion_error")
            self.page.wait_for_timeout(700)
        self.fallar("OXXO no habilitó 'Continuar' tras validar el ticket.", "03_validacion_error")

    def llenar_datos_fiscales(self, sol):
        """Llena lo que el portal deje editar ANTES de Continuar (con sesión, la sección 2 puede
        estar habilitada). Devuelve True si quedó algo por llenar después."""
        if self.page.locator(_sel("form:rfcs_input")).count():
            r = self.pf_select("form:rfcs", valor=self.datos.rfc)
            if r and r.get("texto"):
                self.ui.evento(f"RFC elegido de tu cuenta: {self.datos.rfc}")
                self.esperar_ajax(1500)
        for id_, attr in CAMPOS_FISCALES.items():
            valor = getattr(self.datos, attr)
            loc = self.page.locator(_sel(id_))
            if valor and loc.count() and self.habilitado(id_) and not loc.first.input_value().strip():
                self.escribir(id_, valor)
        if self.habilitado("form:estado_input") and not self.texto_select("form:estado"):
            self.pf_select("form:estado", contiene=_norm(self.datos.estado))
        if self.habilitado("form:selectOneMenuRegFis_input"):
            self.pf_select("form:selectOneMenuRegFis", contiene=self.datos.regimen_texto)
            self.esperar_ajax(1000)
        if self.habilitado("form:selectOneMenuCFDI_input"):
            r = self.pf_select("form:selectOneMenuCFDI", contiene=USO_CFDI_TEXTO[sol.uso_cfdi])
            if r is not None and not r.get("texto"):
                self.fallar(f"No encontré el uso CFDI {sol.uso_cfdi}. Opciones: {r.get('opciones')}",
                            "04_uso_error")
            self.esperar_ajax(800)

    def leer_datos_fiscales_oxxo(self):
        datos = {}
        for id_, etiqueta in ETIQUETAS_FISCALES.items():
            loc = self.page.locator(_sel(id_))
            if loc.count():
                v = loc.first.input_value().strip()
                if v:
                    datos[etiqueta] = v
        for base, etiqueta in (("form:estado", "Estado"), ("form:selectOneMenuRegFis", "Régimen fiscal"),
                               ("form:selectOneMenuCFDI", "Uso CFDI")):
            v = self.texto_select(base)
            if v:
                datos[etiqueta] = v
        return datos

    def confirmar_y_facturar(self, sol):
        self.resumen["datos_fiscales_portal"] = self.leer_datos_fiscales_oxxo()
        self.resumen["captura_datos_fiscales"] = self.ui.captura(self.page, "04_listo_para_continuar")
        uso_portal = self.resumen["datos_fiscales_portal"].get("Uso CFDI")
        if uso_portal and USO_CFDI_TEXTO[sol.uso_cfdi] not in _norm(uso_portal):
            self.fallar(f"El portal tiene uso CFDI '{uso_portal}', distinto de {sol.uso_cfdi}.",
                        "04_uso_distinto")

        # Paso irreversible (en OXXO, "Continuar" puede generar la factura de inmediato).
        r = self.ui.preguntar(
            "confirmar_facturar",
            "Ticket validado por OXXO. ¿Facturar? En OXXO el clic en 'Continuar' puede generar la "
            "factura DEFINITIVAMENTE de inmediato; cada ticket solo se factura una vez.",
            ["facturar", "cancelar"],
            captura=self.resumen["captura_datos_fiscales"],
            datos={**self.resumen, "mensajes_del_portal": list(self.mensajes_portal)},
        )
        if r != "facturar":
            raise FlujoCancelado("Cancelado por el usuario antes de facturar.")

        self.page.locator(_sel("form:continuar")).first.click()
        self.ui.marcar_facturado()  # desde aquí no se puede deshacer
        self.ui.evento("Se dio clic en 'Continuar' (OXXO).")
        self.esperar_ajax(3000, timeout_ms=90000)
        self.ui.captura(self.page, "05_despues_de_continuar")

        # Sin sesión (o si el portal así lo pide): falta "Generar Factura" y "Verifica tus datos".
        if self.habilitado("form:generarFactura"):
            self.llenar_datos_fiscales(sol)
            self.page.locator(_sel("form:generarFactura")).first.click()
            self.esperar_ajax(2500)
            if self.dialogo_visible("verifica tus datos"):
                self.ui.captura(self.page, "06_verifica_tus_datos")
                self.clic_en_dialogo("verifica tus datos", "Si")
                self.esperar_ajax(3000, timeout_ms=90000)
            msgs = self.mensajes_growl()
            if msgs and not self.dialogo_visible("correo"):
                self.fallar(f"OXXO respondió: {' '.join(msgs)}", "07_generar_error")

        self.ui.captura(self.page, "08_resultado_final")
        texto_final = self.texto_visible(1200)
        if self.dialogo_visible("se enviará la factura"):
            self.clic_en_dialogo("se enviará la factura", "OK")
        return texto_final

    # ---------------------------------------------------------------- flujo

    def ejecutar(self, url, sol):
        self.resumen = {"comercio": "OXXO", "fecha": sol.fecha.strftime("%d/%m/%Y"),
                        "folio": sol.folio, "id_venta": sol.id_venta, "total": f"{sol.total:.2f}",
                        "rfc": self.datos.rfc, "razon_social": self.datos.razon_social,
                        "uso_cfdi": sol.uso_cfdi, "entrega": "email"}
        self.abrir_oxxo(url)
        self.iniciar_sesion()
        self.ui.captura(self.page, "01_formulario")
        self.pantalla_ticket_oxxo(sol)
        self.llenar_datos_fiscales(sol)
        texto_final = self.confirmar_y_facturar(sol)
        return {"texto_final": texto_final,
                "elementos": [e for e in self.listar_elementos("input, select, button, a") if e["visible"]]}
