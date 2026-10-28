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


# Cómo reconocer cada pantalla: por su ruta O por un elemento propio de ella. El portal
# puede cambiar de pantalla sin cambiar la URL, así que no basta con revisar la ruta.
PANTALLAS = {
    "datos fiscales": ("/address", "[name='Razón Social']"),
    "forma de pago": ("/payment", "select:has(option[value='28'])"),
    "entrega de la factura": ("/invoiceSelection", "#method_pdf_radio, #method_email_radio, input[type=radio]"),
}

# Opciones de entrega: primero el id conocido; si el portal lo cambió, el texto de la opción.
# En oct-2026 la de correo ya no tenía el id method_email_radio ("Enviar a correo electrónico").
OPCIONES_ENTREGA = {
    "email": ("#method_email_radio", re.compile(r"enviar a correo", re.I), "por correo"),
    "descarga": ("#method_pdf_radio", re.compile(r"^\s*pdf\s*$", re.I), "PDF"),
}

# El portal cambia la URL (/payment, /invoiceSelection) antes de dibujar sus controles:
# hay que esperarlos en vez de buscarlos una sola vez.
ESPERA_CONTROLES_MS = 15000

# Avisos emergentes: el conocido (#popup_btn_accept) y cualquier "Aceptar" dentro del overlay.
SEL_AVISO = "#popup_btn_accept, #popup_overlay button:has-text('Aceptar')"

# Mensajes del portal que SÍ se le preguntan a un humano; los demás (confirmaciones
# rutinarias) se aceptan solos y se muestran en la confirmación final antes de facturar.
RE_MODAL_DELICADO = re.compile(
    r"refactur|cancel|sustitu|ya (fue|ha sido|est[aá]|se encuentra) factur|no es posible|"
    r"no se puede|no coincide|error|incorrect|inv[aá]lid|rechaz", re.I)

USO_CFDI_BUSQUEDA = {"G01": "adquisici", "G03": "general"}
FORMAS_PAGO = {"01": "Efectivo", "04": "Tarjeta de crédito", "28": "Tarjeta de débito",
               "05": "Monedero electrónico"}

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
class SolicitudFactura:
    tc: str
    tr: str
    uso_cfdi: str            # "G01" | "G03"
    forma_pago: str          # "01" | "04" | "28" | "05"
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
        self.mensajes_portal = []  # modales aceptados automáticamente

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
        return self.pantalla_siguiente((nombre,), timeout_ms) == nombre

    def pantalla_siguiente(self, nombres, timeout_ms=10000):
        """Espera a que aparezca cualquiera de las pantallas `nombres`; devuelve cuál, o None.

        Manda el contenido (un control propio de la pantalla): el portal real a veces muestra
        una URL que no corresponde a lo que se ve. La URL solo decide si al agotarse el tiempo
        ningún contenido apareció."""
        fin = time.time() + timeout_ms / 1000
        while True:
            for nombre in nombres:
                _, selector = PANTALLAS[nombre]
                if self.page.locator(selector).locator("visible=true").count():
                    self.ui.evento(f"Pantalla '{nombre}' reconocida por su contenido.")
                    return nombre
            if "/formerror" in self.page.url.lower():
                return None
            if time.time() > fin:
                for nombre in nombres:
                    if PANTALLAS[nombre][0].lower() in self.page.url.lower():
                        self.ui.evento(f"Pantalla '{nombre}' reconocida solo por la URL ({self.page.url}).")
                        return nombre
                return None
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
        """Modales encadenados del portal. Las confirmaciones rutinarias se aceptan solas
        (quedan en eventos y en la confirmación final); solo se le pregunta a un humano si
        el texto suena delicado (refacturar, cancelar, error...) o no se pudo leer."""
        for i in range(1, maximo + 1):
            primario = self.page.locator("#dynamic_modal_primary_btn")
            if not (primario.count() and primario.first.is_visible()):
                return
            # Se sube desde el botón hasta el primer contenedor que tenga texto además de los
            # botones (un selector tipo [class*=modal] atrapaba solo el "modal-footer").
            texto = primario.first.evaluate(
                """e => {
                    let c = e.parentElement;
                    for (let i = 0; i < 10 && c && c !== document.body; i++, c = c.parentElement) {
                        let t = c.innerText || '';
                        for (const b of c.querySelectorAll('button')) t = t.replace(b.innerText, '');
                        if (t.trim()) return (c.innerText || '').trim();
                    }
                    return '';
                }"""
            )
            captura = self.ui.captura(self.page, f"{paso}_modal_{i}")
            if texto and not RE_MODAL_DELICADO.search(texto):
                self.mensajes_portal.append(texto)
                self.ui.evento(f"Mensaje del portal aceptado automáticamente: {texto[:300]}")
                r = "continuar"
            else:
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

    def abrir(self, url):
        self.page.goto(url, wait_until="networkidle", timeout=60000)
        esperar(self.page, 1000)
        self.cerrar_popup(espera_ms=6000)
        for sel in ("#obtener_factura_button", "#invoice_tab_facturar"):
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
                             "la factura está en el correo que se usó al facturar.")

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

        # Lo que realmente quedó en el portal (no lo configurado), para revisarlo antes de facturar.
        self.resumen["datos_fiscales_portal"] = self.leer_datos_fiscales_portal()
        self.resumen["captura_datos_fiscales"] = self.ui.captura(self.page, "04_direccion_llenada")
        self.revisar_captcha("direccion")
        self.clic("#form_btn_accept", "'Aceptar' de /address")
        if self.hay_captcha():
            self.revisar_captcha("direccion_enviada")
            self.clic("#form_btn_accept", "'Aceptar' de /address")
        self.manejar_modales("05_direccion")
        # Con pago en efectivo el portal se salta la forma de pago y va directo a la entrega.
        siguiente = self.pantalla_siguiente(("forma de pago", "entrega de la factura"))
        if siguiente is None:
            self.exigir_pantalla("forma de pago", "05_no_avanzo")  # reporta el error con detalle
        return siguiente

    def leer_datos_fiscales_portal(self):
        datos = {}
        for etiqueta, name in (("RFC", "rfc"), ("Razón social", "Razón Social"), ("Calle", "Calle"),
                               ("Núm. exterior", "Número exterior"), ("Núm. interior", "Número interior"),
                               ("Colonia", "Colonia"), ("Municipio", "Municipio/Delegación"),
                               ("Estado", "Estado"), ("Código postal", "Código Postal"),
                               ("Correo", "email")):
            loc = self.page.locator(f"[name='{name}']")
            valor = loc.first.input_value().strip() if loc.count() else ""
            if valor:
                datos[etiqueta] = valor
        for etiqueta, name in (("Régimen fiscal", "Régimen Fiscal"), ("Uso CFDI", "Uso Factura")):
            loc = self.page.locator(f"[name='{name}']")
            if loc.count():
                texto = loc.first.evaluate(
                    "e => e.selectedIndex >= 0 && e.value ? e.options[e.selectedIndex].text.trim() : ''")
                if texto:
                    datos[etiqueta] = texto
        return datos

    def esperar_control(self, selector):
        """Espera a que el control sea visible; False si no aparece a tiempo."""
        try:
            self.page.locator(selector).first.wait_for(state="visible", timeout=ESPERA_CONTROLES_MS)
            return True
        except Exception:
            return False

    def pantalla_pago(self, sol):
        sel = self.page.locator("select")
        if not self.esperar_control("select"):
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

    def elegir_entrega(self, metodo):
        """Marca la opción de entrega. Devuelve cómo la encontró, o None si no está."""
        selector, texto, _ = OPCIONES_ENTREGA[metodo]
        radio = self.page.locator(selector)
        if radio.count():
            radio.first.check()
            return selector
        radio = self.page.get_by_role("radio", name=texto)
        if radio.count():
            radio.first.check()
            return f"opción '{radio.first.evaluate('e => e.labels[0] ? e.labels[0].innerText.trim() : e.value')}'"
        return None

    def opciones_entrega_visibles(self):
        """Para el mensaje de error: qué opciones tiene la pantalla (id, name y texto)."""
        radios = self.page.eval_on_selector_all(
            "input[type=radio]",
            "els => els.map(e => `${e.id || '(sin id)'} name=${e.name || ''} "
            "'${(e.labels[0] ? e.labels[0].innerText : '').trim()}'`)")
        return "; ".join(radios) or "ninguna"

    def pantalla_entrega(self, sol):
        self.esperar_control("input[type=radio]")
        como = self.elegir_entrega(sol.metodo_entrega)
        if not como:
            nombre = OPCIONES_ENTREGA[sol.metodo_entrega][2]
            self.fallar(f"No encontré la opción de entrega {nombre}. "
                        f"Opciones de entrega en la pantalla: {self.opciones_entrega_visibles()}.",
                        "08_entrega_error")
        self.ui.evento(f"Entrega elegida: {como}.")
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
            datos={**self.resumen, "mensajes_del_portal": list(self.mensajes_portal)},
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
        if self.pantalla_direccion(sol) == "forma de pago":
            self.pantalla_pago(sol)
        else:
            self.resumen["forma_pago"] = "La define el ticket (el portal no la pidió; p. ej. efectivo)"
            self.ui.evento("El portal no pidió forma de pago; pasó directo a la entrega.")
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
