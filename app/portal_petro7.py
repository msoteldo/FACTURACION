"""Automatización del portal de facturación de Petro Seven (AngularJS, una sola página).

Con sesión iniciada (correo y contraseña de la cuenta del cliente):
  1. Menú "Facturar": No. Estación, Folio, Web ID y Fecha del ticket, y "Agregar Ticket"
     (solo valida el ticket con el portal; no factura).
  2. Datos de facturación: el portal los trae de la cuenta (RFC, razón social, régimen,
     domicilio, correo). Se eligen forma de pago (si la pide) y uso CFDI.
  3. "Facturar": emite el CFDI de inmediato, sin confirmación del portal. Por eso la
     confirmación humana va ANTES de ese clic.

El portal tiene un detector de bots. Si en lugar del portal aparece su página de bloqueo,
el trabajo termina con error: no se intenta pasar el bloqueo ni resolver su captcha.
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from .portal_walmart import FlujoCancelado, FlujoError, FlujoWalmart

USO_CFDI_CLAVE = {"G01": "G01", "G03": "G03"}
FORMA_PAGO_TEXTO = {"04": "crédito", "28": "débito"}

# Página de bloqueo del detector de bots (Radware / perfdrive).
RE_BLOQUEO = re.compile(r"anomaly detected|think that you are a bot|request unblock", re.I)

SEL_LOGIN_CORREO = "[ng-model='email']"
SEL_LOGIN_PASSWORD = "[ng-model='password']"
SEL_LOGIN_BOTON = "[ng-click='bannerCtrl.loginUser()']"
SEL_ESTACION = "[ng-model='selectedEmployee.noEstacion']"
SEL_FOLIO = "[ng-model='selectedEmployee.noTicket']"
SEL_WEB_ID = "[ng-model='selectedEmployee.wid']"
SEL_FECHA = "[ng-model='selectedEmployee.fechaTicket']"
SEL_AGREGAR = "[ng-click='addRow()']"
SEL_FACTURAR = "[ng-click^='facturacionCtrl.facturarTicket']"


def _norm(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower().strip()


@dataclass
class SolicitudPetro7:
    estacion: str       # No. Estación
    folio: str          # Folio del ticket (noTicket)
    web_id: str         # Web ID impreso en el ticket
    fecha: date
    uso_cfdi: str       # "G01" | "G03"
    forma_pago: str = ""   # "04" | "28" si el portal la pide; vacío = la define el ticket
    metodo_entrega: str = "email"  # Petro Seven la envía al correo de la cuenta

    @property
    def tc(self):
        """Llave para evitar dos trabajos simultáneos del mismo ticket."""
        return f"P7-{self.estacion}-{self.folio}"


class FlujoPetro7(FlujoWalmart):
    def __init__(self, page, datos, interaccion, usuario="", password=""):
        super().__init__(page, datos, interaccion)
        self.usuario = usuario
        self.password = password
        self.alertas = []
        page.on("dialog", self._alerta)

    # ---------------------------------------------------------------- utilidades

    def _alerta(self, dialogo):
        """Los avisos de validación del portal llegan como window.alert: se guardan y se cierran."""
        self.alertas.append(dialogo.message.strip())
        self.ui.evento(f"Aviso del portal: {dialogo.message.strip()[:300]}")
        dialogo.accept()

    def visible(self, selector):
        loc = self.page.locator(selector).locator("visible=true")
        return loc.first if loc.count() else None

    def revisar_bloqueo(self, paso):
        if RE_BLOQUEO.search(self.texto_visible(2000)):
            self.fallar("El portal de Petro Seven bloqueó el acceso automático (detector de bots). "
                        "Este ticket hay que facturarlo a mano.", f"{paso}_bloqueo")

    def escribir(self, selector, valor, nombre):
        campo = self.visible(selector)
        if not campo:
            self.fallar(f"No encontré el campo {nombre}.", "02_campo_no_encontrado")
        campo.fill(str(valor))
        campo.dispatch_event("change")
        campo.dispatch_event("blur")

    def poner_fecha(self, fecha):
        """md-datepicker: con AngularJS se fija el modelo; si no, se escribe en su input."""
        ok = self.page.evaluate(
            """([sel, iso]) => {
                const els = Array.from(document.querySelectorAll(sel)).filter(e => e.offsetParent !== null);
                if (!els.length) return false;
                const el = els[0];
                if (window.angular) {
                    const ctrl = window.angular.element(el).controller('ngModel');
                    if (ctrl) {
                        const [y, m, d] = iso.split('-').map(Number);
                        ctrl.$setViewValue(new Date(y, m - 1, d));
                        window.angular.element(el).scope().$apply();
                        return true;
                    }
                }
                const input = el.querySelector('input') || el;
                const [y, m, d] = iso.split('-');
                input.value = `${d}/${m}/${y}`;
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""", [SEL_FECHA, fecha.isoformat()])
        if not ok:
            self.fallar("No encontré la fecha del ticket.", "02_campo_no_encontrado")

    def elegir_select(self, selector, contiene, nombre):
        sel = self.visible(selector)
        if not sel:
            return None
        opciones = sel.evaluate("e => Array.from(e.options).map(o => o.text.trim())")
        elegida = next((o for o in opciones if _norm(contiene) in _norm(o)), None)
        if not elegida:
            self.fallar(f"No encontré la opción '{contiene}' en {nombre}. Opciones: {opciones}",
                        "03_opcion_no_encontrada")
        sel.select_option(label=elegida)
        return elegida

    def valor(self, selector):
        campo = self.visible(selector)
        if not campo:
            return ""
        return campo.evaluate(
            "e => e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {text: ''}).text.trim() : e.value.trim()")

    # ---------------------------------------------------------------- pasos

    def abrir_petro7(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3000)
        self.revisar_bloqueo("01")
        self.revisar_captcha("inicio")

    def iniciar_sesion(self):
        if not (self.usuario and self.password):
            self.fallar("Faltan PETRO7_USUARIO y PETRO7_PASSWORD: el portal solo factura sin "
                        "captcha con sesión iniciada.", "01_sin_credenciales")
        self.escribir(SEL_LOGIN_CORREO, self.usuario, "correo de la cuenta")
        self.escribir(SEL_LOGIN_PASSWORD, self.password, "contraseña")
        boton = self.visible(SEL_LOGIN_BOTON)
        if not boton:
            self.fallar("No encontré el botón para iniciar sesión.", "01_login_error")
        boton.click()
        self.page.wait_for_timeout(5000)
        self.revisar_bloqueo("01_login")
        if self.visible(SEL_LOGIN_BOTON):
            aviso = self.alertas[-1] if self.alertas else ""
            self.fallar(f"No se pudo iniciar sesión en Petro Seven. {aviso}".strip(), "01_login_error")
        self.ui.evento("Sesión iniciada en Petro Seven.")

    def abrir_facturar(self):
        if self.visible(SEL_ESTACION):
            return
        for opcion in self.page.get_by_text(re.compile(r"^\s*Facturar\s*$", re.I)).all():
            es_boton_final = opcion.evaluate(
                "e => (e.getAttribute('ng-click') || '').includes('facturarTicket')")
            if opcion.is_visible() and not es_boton_final:
                opcion.click()
                break
        self.page.wait_for_timeout(2500)
        if not self.visible(SEL_ESTACION):
            self.fallar("No encontré el formulario para facturar.", "02_sin_formulario")

    def agregar_ticket(self, sol):
        self.escribir(SEL_ESTACION, sol.estacion, "No. Estación")
        self.escribir(SEL_FOLIO, sol.folio, "Folio")
        self.escribir(SEL_WEB_ID, sol.web_id, "Web ID")
        self.poner_fecha(sol.fecha)
        self.ui.captura(self.page, "02_ticket_capturado")
        antes = len(self.alertas)
        boton = self.visible(SEL_AGREGAR)
        if not boton:
            self.fallar("No encontré el botón 'Agregar Ticket'.", "02_sin_agregar")
        boton.click()    # solo valida el ticket con el portal; no factura
        self.page.wait_for_timeout(4000)
        nuevas = self.alertas[antes:]
        if nuevas:
            self.fallar(f"Petro Seven no aceptó el ticket: {' '.join(nuevas)}", "02_ticket_rechazado")
        if sol.folio not in self.texto_visible(6000):
            self.fallar("El ticket no apareció en la lista después de agregarlo.", "02_ticket_no_agregado")
        self.ui.evento("Ticket validado y agregado por Petro Seven.")

    def datos_de_facturacion(self, sol):
        if self.visible("#formaPago"):
            if sol.forma_pago not in FORMA_PAGO_TEXTO:
                self.fallar("El portal pide forma de pago (crédito o débito) y no se indicó.",
                            "03_falta_forma_pago")
            self.resumen["forma_pago"] = self.elegir_select("#formaPago", FORMA_PAGO_TEXTO[sol.forma_pago],
                                                            "Forma de pago")
        else:
            self.resumen["forma_pago"] = self.valor("#formaPagoAux") or "La define el ticket"
        uso = self.elegir_select("#usoCfdi", USO_CFDI_CLAVE[sol.uso_cfdi], "Uso CFDI")
        if not uso:
            self.fallar("No encontré el Uso CFDI.", "03_sin_uso")
        self.resumen["uso_cfdi_portal"] = uso
        fiscales = {"RFC": self.valor("#rfcCliente"), "Razón social": self.valor("#razon"),
                    "Régimen fiscal": self.valor("#regimenFiscalReceptor"), "Uso CFDI": uso,
                    "Código postal": self.valor("#cp"), "Correo": self.valor("#emailInput")}
        self.resumen["datos_fiscales_portal"] = {k: v for k, v in fiscales.items() if v}
        rfc_portal = fiscales["RFC"].upper()
        if rfc_portal and self.datos.rfc and rfc_portal != self.datos.rfc.upper():
            r = self.ui.preguntar(
                "revisar_direccion",
                "La cuenta de Petro Seven tiene un RFC distinto del configurado. "
                "¿Continuar con el de la cuenta?",
                ["continuar", "cancelar"],
                captura=self.ui.captura(self.page, "03_rfc_distinto"),
                datos={"RFC": {"esperado": self.datos.rfc, "en_portal": rfc_portal}},
            )
            if r != "continuar":
                raise FlujoCancelado("Cancelado: el RFC de la cuenta de Petro Seven no coincide.")
        self.resumen["captura_datos_fiscales"] = self.ui.captura(self.page, "03_listo_para_facturar")

    def confirmar_y_facturar(self):
        boton = self.visible(SEL_FACTURAR)
        if not boton:
            self.fallar("No encontré el botón 'Facturar'.", "04_sin_facturar")
        self.revisar_captcha("antes_de_facturar")
        # Paso irreversible: el clic en "Facturar" emite el CFDI de inmediato.
        r = self.ui.preguntar(
            "confirmar_facturar",
            "Ticket validado por Petro Seven. ¿Dar clic en 'Facturar'? Esto genera la factura "
            "DEFINITIVAMENTE y cada ticket solo se puede facturar una vez.",
            ["facturar", "cancelar"],
            captura=self.resumen["captura_datos_fiscales"],
            datos=dict(self.resumen),
        )
        if r != "facturar":
            raise FlujoCancelado("Cancelado por el usuario antes de facturar.")
        antes = len(self.alertas)
        boton.click()
        self.ui.marcar_facturado()
        self.ui.evento("Se dio clic en 'Facturar' (Petro Seven).")
        self.page.wait_for_timeout(8000)
        self.ui.captura(self.page, "05_resultado")
        nuevas = self.alertas[antes:]
        if nuevas:
            raise FlujoError(f"Petro Seven respondió: {' '.join(nuevas)}")
        return self.texto_visible(1200)

    # ---------------------------------------------------------------- flujo

    def ejecutar(self, url, sol):
        self.resumen = {"comercio": "Petro Seven", "estacion": sol.estacion, "folio": sol.folio,
                        "web_id": sol.web_id, "fecha": sol.fecha.strftime("%d/%m/%Y"),
                        "rfc": self.datos.rfc, "razon_social": self.datos.razon_social,
                        "uso_cfdi": sol.uso_cfdi, "entrega": "email"}
        self.abrir_petro7(url)
        self.iniciar_sesion()
        self.abrir_facturar()
        self.agregar_ticket(sol)
        self.datos_de_facturacion(sol)
        return {"texto_final": self.confirmar_y_facturar()}
