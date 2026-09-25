"""Automatización del portal de facturación de Sam's Club / Walmart México.

Es el mismo flujo de `facturar_sams.py`, pero sin input(): las decisiones que antes se
preguntaban en la terminal llegan como parámetros (SolicitudFactura), y lo que sí
requiere a un humano (modales del portal, captchas y el clic final en "Facturar")
se delega a un objeto `Interaccion`, que en el servidor pausa el trabajo hasta que
alguien responda por la API.
"""
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeout

# Nombres de los inputs de /ticket.
CAMPOS_TICKET = {
    "MEMBRESIA_O_RFC": "membershipOrRFC",
    "CP": "postalCode",
    "TC": "ticketNumber",
    "TR": "transactionNumber",
}

# Campos de /address. El "name" es literalmente la etiqueta visible (con acentos).
CAMPOS_DIRECCION = {
    "rfc": "rfc",
    "razon_social": "Razón Social",
    "calle": "Calle",
    "num_ext": "Número exterior",
    "num_int": "Número interior",
    "referencia": "Referencia",
    "estado": "Estado",
    "municipio": "Municipio/Delegación",
    "colonia": "Colonia",
    "cp": "Código Postal",
    "email": "email",
}

# Pestaña "Consulta o reenvía tu factura".
CAMPO_CONSULTA = "numeroDeTicketoFactura"
RE_DESCARGA = re.compile(r"descarg|download|\bpdf\b|\bxml\b", re.I)
RE_NO_TOCAR = re.compile(r"reenv|enviar|correo|e-?mail|cancel|elimin|borrar|refactur", re.I)

# Cómo reconocer cada pantalla: por su ruta O por un elemento propio de ella. El portal
# puede cambiar de pantalla sin cambiar la URL, así que no basta con revisar la ruta.
PANTALLAS = {
    "datos fiscales": ("/address", "[name='Razón Social']"),
    "forma de pago": ("/payment", "select:has(option[value='28'])"),
    "entrega de la factura": ("/invoiceSelection", "#invoice_form_btn_submit, #method_pdf_radio"),
}

# Avisos emergentes: el conocido (#popup_btn_accept) y cualquier "Aceptar" dentro del overlay.
SEL_AVISO = "#popup_btn_accept, #popup_overlay button:has-text('Aceptar')"

USO_CFDI_BUSQUEDA = {"G01": "adquisici", "G03": "general"}
FORMAS_PAGO = {"04": "Tarjeta de crédito", "28": "Tarjeta de débito", "05": "Monedero electrónico"}

SEL_CAPTCHA = (
    "iframe[src*='captcha' i]:not([src*='size=invisible']), "
    "iframe[title*='captcha' i]:not([src*='size=invisible']), "
    "[id*='captcha' i]:not(script), [class*='captcha' i]:not(script):not(.grecaptcha-badge)"
)


class FlujoCancelado(Exception):
    """Un humano canceló (o no respondió a tiempo). No se facturó nada."""


class FlujoError(Exception):
    """El portal no se comportó como se esperaba."""


@dataclass
class SolicitudConsulta:
    tc: str                  # número de ticket (TC#) o folio de la factura


@dataclass
class SolicitudFactura:
    tc: str
    tr: str
    uso_cfdi: str            # "G01" | "G03"
    forma_pago: str          # "04" | "28" | "05"
    metodo_entrega: str      # "email" | "descarga"
    correo_alterno: Optional[str] = None


def _normaliza(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


def esperar(page, ms=2000):
    """El portal es una SPA en React: networkidle no basta, hay que dar tiempo a que
    renderice la pantalla nueva antes de inspeccionar el DOM."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    page.wait_for_timeout(ms)


class FlujoWalmart:
    def __init__(self, page, datos, interaccion):
        self.page = page
        self.datos = datos
        self.ui = interaccion

    # ---------------------------------------------------------------- utilidades

    def llenar_por_name(self, name, valor, etiqueta):
        loc = self.page.locator(f"[name='{name}']")
        if loc.count() == 0:
            return False
        if loc.count() > 1:
            visibles = loc.locator("visible=true")
            loc = visibles if visibles.count() else loc
        el = loc.first

        # Campos como RFC en /address vienen prellenados y bloqueados por el portal:
        # no se escriben, solo se valida que coincidan.
        if el.is_disabled():
            actual = el.input_value()
            if actual.upper() == valor.upper():
                self.ui.evento(f"'{etiqueta}' viene prellenado y bloqueado por el portal; coincide.")
                return True
            self.ui.evento(f"[ALERTA] '{etiqueta}' está bloqueado con '{actual}', distinto de '{valor}'.")
            return False

        try:
            el.fill(valor, timeout=5000)
        except Exception as e:
            self.ui.evento(f"[ALERTA] no se pudo escribir en '{etiqueta}': {type(e).__name__}")
            return False
        if el.input_value() != valor:
            self.ui.evento(f"[ALERTA] '{etiqueta}' no conservó el valor escrito.")
            return False
        return True

    def elegir_opcion_por_texto(self, name, contiene):
        loc = self.page.locator(f"[name='{name}']")
        if not loc.count():
            return None
        ops = loc.first.evaluate("e => Array.from(e.options).map(o => ({value: o.value, text: o.text}))")
        objetivo = _normaliza(contiene)
        for o in ops:
            if o["value"] and objetivo in _normaliza(o["text"]):
                loc.first.select_option(value=o["value"])
                self.ui.evento(f"'{name}' = {o['text']}")
                return o["text"]
        self.ui.evento(f"[ALERTA] '{name}' no tiene opción que contenga '{contiene}'. "
                       f"Opciones: {[o['text'] for o in ops]}")
        return None

    def cerrar_popup(self, espera_ms=0):
        """El aviso del portal puede aparecer un momento DESPUÉS de cargar la página, y su
        overlay (#popup_overlay) intercepta todos los clics hasta que se acepta."""
        boton = self.page.locator(SEL_AVISO).locator("visible=true")
        if espera_ms:
            try:
                boton.first.wait_for(state="visible", timeout=espera_ms)
            except PWTimeout:
                pass
        if not boton.count():
            return False
        boton.first.click()
        try:  # el overlay se desvanece con una animación
            self.page.locator("#popup_overlay").first.wait_for(state="hidden", timeout=5000)
        except PWTimeout:
            pass
        esperar(self.page, 500)
        return True

    def clic(self, selector, descripcion, ms=2000):
        self.cerrar_popup()
        boton = self.page.locator(selector)
        if not boton.count():
            self.fallar(f"No encontré el botón {descripcion} ({selector}).", "boton_no_encontrado")
        boton.first.click()
        esperar(self.page, ms)

    def texto_visible(self, limite=1500):
        try:
            return self.page.locator("body").inner_text(timeout=3000)[:limite]
        except Exception:
            return ""

    def fallar(self, mensaje, captura):
        self.ui.captura(self.page, captura)
        texto = self.texto_visible()
        raise FlujoError(f"{mensaje}\nTexto visible en el portal:\n{texto}" if texto else mensaje)

    def en_pantalla(self, nombre, timeout_ms=10000):
        """Espera a que el portal muestre la pantalla `nombre` (ver PANTALLAS)."""
        ruta, selector = PANTALLAS[nombre]
        fin = time.time() + timeout_ms / 1000
        while True:
            if ruta.lower() in self.page.url.lower():
                return True
            loc = self.page.locator(selector).locator("visible=true")
            if loc.count():
                return True
            if "/formerror" in self.page.url.lower() or time.time() > fin:
                return False
            self.page.wait_for_timeout(500)

    def exigir_pantalla(self, nombre, captura, pista=""):
        if self.en_pantalla(nombre):
            return
        self.revisar_error_portal(captura)
        self.fallar(f"El portal no avanzó a la pantalla de {nombre} (URL: {self.page.url}).{pista}",
                    captura)

    def revisar_error_portal(self, captura):
        """Cuando el portal rechaza algo, navega a /formError con un mensaje y un botón
        #error_btn_accept. Se reporta como error con el mensaje literal del portal."""
        if "/formerror" not in self.page.url.lower():
            return
        mensaje = ""
        boton = self.page.locator("#error_btn_accept")
        if boton.count():
            mensaje = boton.first.evaluate(
                """e => {
                    let c = e.parentElement;
                    for (let i = 0; i < 4 && c && !(c.innerText || '').replace(e.innerText, '').trim(); i++)
                        c = c.parentElement;
                    return c ? c.innerText.replace(e.innerText, '').trim() : '';
                }""")
        self.ui.captura(self.page, captura)
        raise FlujoError(f"El portal respondió: {mensaje or self.texto_visible(500)}")

    def hay_captcha(self):
        # Solo cuenta un captcha *visible*: el badge invisible de reCAPTCHA v3 que muchos
        # sitios cargan siempre no requiere a un humano.
        loc = self.page.locator(SEL_CAPTCHA)
        return any(loc.nth(i).is_visible() for i in range(min(loc.count(), 10)))

    def revisar_captcha(self, paso):
        """Nunca se intenta resolver un captcha: se pausa y se avisa a un humano."""
        while self.hay_captcha():
            captura = self.ui.captura(self.page, f"captcha_{paso}")
            r = self.ui.preguntar(
                "captcha",
                "El portal mostró un captcha. El servicio no los resuelve. Puedes reintentar "
                "(se vuelve a revisar la página) o cancelar y facturar este ticket a mano.",
                ["reintentar", "cancelar"],
                captura=captura,
            )
            if r == "cancelar":
                raise FlujoCancelado("Cancelado por captcha.")
            esperar(self.page, 3000)  # sin recargar: se perdería lo ya capturado en la SPA

    def manejar_modales(self, paso, maximo=3):
        """Modales encadenados del portal: se muestran a un humano, que decide."""
        for i in range(1, maximo + 1):
            primario = self.page.locator("#dynamic_modal_primary_btn")
            if not (primario.count() and primario.first.is_visible()):
                return
            texto = primario.first.evaluate(
                """e => {
                    const m = e.closest('[role=dialog], .modal, [class*=modal]');
                    return ((m || e.parentElement.parentElement || e.parentElement).innerText || '').trim();
                }"""
            )
            captura = self.ui.captura(self.page, f"{paso}_modal_{i}")
            r = self.ui.preguntar("modal", texto or "(no se pudo leer el texto del modal)",
                                  ["continuar", "cerrar"], captura=captura)
            if r == "continuar":
                primario.first.click()
            else:
                sec = self.page.locator("#dynamic_modal_secondary_btn")
                if sec.count() and sec.first.is_visible():
                    sec.first.click()
                esperar(self.page)
                return
            esperar(self.page)

    # ---------------------------------------------------------------- pantallas

    def abrir(self, url, pestana="#invoice_tab_facturar"):
        self.page.goto(url, wait_until="networkidle", timeout=60000)
        esperar(self.page, 1000)
        self.cerrar_popup(espera_ms=6000)
        for sel in ("#obtener_factura_button", pestana):
            self.cerrar_popup()
            loc = self.page.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click()
                esperar(self.page, 800)
                self.cerrar_popup(espera_ms=1500)
        self.ui.captura(self.page, "01_formulario")

    def pantalla_ticket(self, sol):
        valores = {"MEMBRESIA_O_RFC": self.datos.rfc, "CP": self.datos.cp, "TC": sol.tc, "TR": sol.tr}
        faltan = [k for k, name in CAMPOS_TICKET.items()
                  if not self.llenar_por_name(name, valores[k], k)]
        if faltan:
            self.fallar(f"No pude llenar los campos {faltan} en /ticket.", "02_ticket_error")
        self.ui.captura(self.page, "02_ticket_llenado")
        self.revisar_captcha("ticket")
        self.clic("#form_btn_accept", "'Continuar' de /ticket")
        if self.hay_captcha():  # el captcha suele aparecer justo al enviar
            self.revisar_captcha("ticket_enviado")
            self.clic("#form_btn_accept", "'Continuar' de /ticket")
        self.manejar_modales("03_ticket")
        self.revisar_error_portal("03_error_portal")
        self.exigir_pantalla("datos fiscales", "03_no_avanzo",
                             " ¿TC/TR incorrectos o ticket ya facturado? Si ya está facturado, "
                             "usa POST /consultas para recuperar la factura.")

    def pantalla_direccion(self, sol):
        # Aviso "Importante: ... capturar nuevamente su información fiscal" (sale con retraso).
        self.cerrar_popup(espera_ms=3000)
        faltan = []
        for campo, name in CAMPOS_DIRECCION.items():
            valor = getattr(self.datos, campo)
            if valor and not self.llenar_por_name(name, valor, campo.upper()):
                faltan.append(campo.upper())

        # Segunda pasada: el portal a veces recalcula campos (p. ej. Razón Social a partir
        # del RFC) un momento después de escribirlos.
        self.page.wait_for_timeout(1500)
        diferencias = {}
        for campo, name in CAMPOS_DIRECCION.items():
            valor = getattr(self.datos, campo)
            loc = self.page.locator(f"[name='{name}']")
            if valor and loc.count():
                actual = loc.first.input_value()
                if actual.upper() != valor.upper():
                    diferencias[campo.upper()] = {"esperado": valor, "en_portal": actual}
        for campo in faltan:
            diferencias.setdefault(campo, {"esperado": getattr(self.datos, campo.lower()),
                                           "en_portal": None})
        if diferencias:
            # Puede ser legítimo (el portal pone la razón social tal como está en el SAT),
            # así que lo decide un humano en vez de abortar o seguir a ciegas.
            captura = self.ui.captura(self.page, "04_direccion_diferencias")
            r = self.ui.preguntar(
                "revisar_direccion",
                "Algunos datos fiscales en el portal no coinciden con los configurados. "
                "¿Continuar con lo que muestra el portal?",
                ["continuar", "cancelar"], captura=captura, datos=diferencias,
            )
            if r != "continuar":
                raise FlujoCancelado("Cancelado por diferencias en datos fiscales.")

        if not self.elegir_opcion_por_texto("Régimen Fiscal", self.datos.regimen_texto):
            self.fallar("No encontré el Régimen Fiscal configurado.", "04_regimen_error")
        # "Uso Factura" viene vacío hasta que se elige el régimen; esperar a que se recalcule.
        self.page.wait_for_timeout(1200)
        uso = self.elegir_opcion_por_texto("Uso Factura", USO_CFDI_BUSQUEDA[sol.uso_cfdi])
        if not uso:
            self.fallar(f"No encontré el Uso de CFDI {sol.uso_cfdi}.", "04_uso_error")
        self.resumen["uso_cfdi_portal"] = uso

        self.ui.captura(self.page, "04_direccion_llenada")
        self.revisar_captcha("direccion")
        self.clic("#form_btn_accept", "'Aceptar' de /address")
        if self.hay_captcha():
            self.revisar_captcha("direccion_enviada")
            self.clic("#form_btn_accept", "'Aceptar' de /address")
        self.manejar_modales("05_direccion")
        self.exigir_pantalla("forma de pago", "05_no_avanzo")

    def pantalla_pago(self, sol):
        sel = self.page.locator("select")
        if not sel.count():
            self.fallar("No encontré el select de forma de pago.", "06_pago_error")
        try:
            sel.first.select_option(value=sol.forma_pago)
        except Exception as e:
            self.fallar(f"No pude seleccionar la forma de pago {sol.forma_pago}: {type(e).__name__}",
                        "06_pago_error")
        self.resumen["forma_pago"] = FORMAS_PAGO[sol.forma_pago]
        self.ui.captura(self.page, "06_pago_elegido")
        self.clic("#form_btn_accept", "'Continuar' de /payment", 2500)
        self.manejar_modales("07_pago")
        self.exigir_pantalla("entrega de la factura", "07_no_avanzo")

    def pantalla_entrega(self, sol):
        radio_id = "#method_email_radio" if sol.metodo_entrega == "email" else "#method_pdf_radio"
        radio = self.page.locator(radio_id)
        if not radio.count():
            self.fallar(f"No encontré la opción de entrega {radio_id}.", "08_entrega_error")
        radio.first.check()
        if sol.metodo_entrega == "email" and sol.correo_alterno:
            campo = self.page.locator("[name='invoice_form_email_alt_input']")
            if not campo.count():
                self.fallar("No encontré el campo de correo alternativo.", "08_entrega_error")
            campo.first.fill(sol.correo_alterno)
        self.resumen["entrega"] = sol.metodo_entrega

        boton = self.page.locator("#invoice_form_btn_submit")
        if not boton.count():
            self.fallar("No encontré el botón 'Facturar'.", "08_entrega_error")
        captura = self.ui.captura(self.page, "08_listo_para_facturar")

        # Paso irreversible: SIEMPRE lo aprueba un humano. No hay parámetro para saltarlo.
        r = self.ui.preguntar(
            "confirmar_facturar",
            "Todo está capturado. ¿Dar clic en 'Facturar'? Esto genera la factura "
            "DEFINITIVAMENTE y cada ticket solo se puede facturar una vez.",
            ["facturar", "cancelar"],
            captura=captura,
            datos=dict(self.resumen),
        )
        if r != "facturar":
            raise FlujoCancelado("Cancelado por el usuario antes de facturar.")

        boton.first.click()
        self.ui.marcar_facturado()
        self.ui.evento("Se dio clic en 'Facturar'.")
        esperar(self.page, 3000)
        self.ui.captura(self.page, "09_resultado_facturacion")
        self.manejar_modales("10_final")
        esperar(self.page, 2000)
        self.ui.captura(self.page, "11_resultado_final")

    # ---------------------------------------------------------------- flujo

    def ejecutar(self, url, sol):
        self.resumen = {"tc": sol.tc, "tr": sol.tr, "rfc": self.datos.rfc,
                        "razon_social": self.datos.razon_social, "uso_cfdi": sol.uso_cfdi}
        self.abrir(url)
        self.pantalla_ticket(sol)
        self.pantalla_direccion(sol)
        self.pantalla_pago(sol)
        self.pantalla_entrega(sol)
        return {"texto_final": self.texto_visible(800)}

    def listar_elementos(self, selector="input, select, textarea, button"):
        return self.page.eval_on_selector_all(
            selector,
            """els => els.map(e => ({tag: e.tagName, type: e.type || '', name: e.name || '',
                                    id: e.id || '', visible: !!(e.offsetWidth || e.offsetHeight),
                                    texto: ['BUTTON', 'A'].includes(e.tagName)
                                           ? (e.innerText || '').trim().slice(0, 40) : ''}))""",
        )

    def consultar(self, url, numero):
        """Pestaña "Consulta o reenvía tu factura": busca un ticket ya facturado y descarga
        lo que el portal ofrezca descargar. Es de solo lectura: nunca da clic en opciones
        de reenvío por correo ni en nada que no parezca una descarga."""
        self.abrir(url, pestana="#invoice_tab_consulta")
        if not self.llenar_por_name(CAMPO_CONSULTA, numero, "Número de ticket o factura"):
            self.fallar("No encontré el campo de consulta (¿cambió el portal?).", "02_consulta_error")
        self.ui.captura(self.page, "02_consulta_llenada")
        self.revisar_captcha("consulta")
        self.clic("#form_btn_accept", "'Continuar' de la consulta", 3000)
        if self.hay_captcha():
            self.revisar_captcha("consulta_enviada")
            self.clic("#form_btn_accept", "'Continuar' de la consulta", 3000)
        self.manejar_modales("03_consulta")
        esperar(self.page, 1500)
        self.revisar_error_portal("04_consulta_error")
        self.ui.captura(self.page, "04_consulta_resultado")

        # Descargas: solo botones/ligas visibles cuyo texto o id hable de descargar/PDF/XML.
        descargas = []
        candidatos = self.page.locator("button, a")
        for i in range(min(candidatos.count(), 60)):
            el = candidatos.nth(i)
            try:
                if not el.is_visible():
                    continue
                etiqueta = " ".join(filter(None, [
                    el.inner_text(timeout=1000).strip(), el.get_attribute("id") or "",
                    el.get_attribute("name") or "", el.get_attribute("title") or ""]))
            except Exception:
                continue
            if RE_DESCARGA.search(etiqueta) and not RE_NO_TOCAR.search(etiqueta):
                el.click()
                descargas.append(etiqueta[:60])
                esperar(self.page, 2500)
        if descargas:
            self.ui.evento(f"Se dio clic en: {descargas}")
            self.ui.captura(self.page, "05_consulta_despues_de_descargar")
        else:
            self.ui.evento("No encontré botones de descarga; revisa la captura y 'elementos'.")

        return {
            "url": self.page.url,
            "descargas_intentadas": descargas,
            "texto_visible": self.texto_visible(1500),
            # Para ir afinando el flujo con la pantalla real de resultados.
            "elementos": [e for e in self.listar_elementos("input, select, textarea, button, a")
                          if e["visible"]],
        }

    def inspeccionar(self, url):
        """Equivalente a `--inspect`: abre el portal y lista campos/botones visibles."""
        self.abrir(url)
        elementos = self.listar_elementos()
        return {
            "url": self.page.url,
            "user_agent": self.page.evaluate("navigator.userAgent"),
            "webdriver": self.page.evaluate("navigator.webdriver"),
            "captcha_visible": self.hay_captcha(),
            "elementos": elementos,
            "texto_visible": self.texto_visible(800),
        }
