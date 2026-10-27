"""Automatización del portal de facturación de H-E-B (Angular Material, 3 pasos, sin captcha).

Pantallas (https://facturacion.heb.com.mx/cli/...):
  1. invoice-create: Sucursal (autocompletar, se busca por código), Ticket, Fecha (datepicker de
     solo lectura) y Venta. "Agregar ticket" solo lo mete a una lista local; "Continuar" valida
     y pasa al paso 2 (no factura).
  2. customer-tax-data ("Paso 2 de 3"): RFC, razón social, CP, régimen fiscal y uso CFDI
     (autocompletar) y correo. "Generar factura" EMITE la factura y no se puede deshacer; el
     portal lo habilita aunque falten campos, así que se validan aquí antes del clic.
  3. invoice-confirm ("Paso 3 de 3"): Serie-Folio, estado y aviso de envío por correo.

La confirmación humana va ANTES de "Generar factura"; desde ese clic el trabajo se marca
como facturado.
"""
from dataclasses import dataclass
from datetime import date

from playwright.sync_api import TimeoutError as PWTimeout

from .portal_walmart import FlujoCancelado, FlujoWalmart, _normaliza, esperar

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]

SEL_MENSAJES = "mat-error, simple-snack-bar, .mat-snack-bar-container, mat-dialog-container, .alert-danger"


@dataclass
class SolicitudHeb:
    sucursal: str       # código de sucursal (p. ej. "2950"); el portal la busca por código
    ticket: str         # número de ticket (primer número de la línea de folio)
    fecha: date
    total: float
    uso_cfdi: str       # "G01" | "G03"
    metodo_entrega: str = "email"  # HEB la envía al correo capturado

    @property
    def tc(self):
        """Llave para evitar dos trabajos simultáneos del mismo ticket."""
        return f"HEB-{self.sucursal}-{self.ticket}"


class FlujoHeb(FlujoWalmart):
    def campo(self, etiqueta):
        return self.page.get_by_label(etiqueta, exact=True)

    def mensajes_error(self):
        return self.page.evaluate(
            """sel => Array.from(document.querySelectorAll(sel)).filter(e => e.offsetWidth > 0)
                        .map(e => e.innerText.trim()).filter(Boolean)""", SEL_MENSAJES)

    def cerrar_aviso(self, espera_ms=3000):
        """Aviso informativo "IMPORTANTE" (botón "Enterado"); solo aparece a veces."""
        boton = self.page.get_by_role("button", name="Enterado")
        try:
            boton.wait_for(state="visible", timeout=espera_ms)
        except PWTimeout:
            return
        self.mensajes_portal.append("Aviso del portal: solo se factura hasta el último día del mes de la compra.")
        boton.click()
        esperar(self.page, 500)

    def escribir(self, etiqueta, valor):
        campo = self.campo(etiqueta)
        campo.fill(str(valor))
        if campo.input_value().strip() != str(valor).strip():
            self.fallar(f"El campo '{etiqueta}' no conservó el valor escrito.", "ticket_error")

    def elegir_autocompletar(self, etiqueta, busqueda, coincide, captura):
        """Escribe `busqueda` en un campo de autocompletar y elige la opción cuyo texto cumple
        `coincide`. Con búsqueda vacía solo abre la lista."""
        campo = self.campo(etiqueta)
        campo.click()
        campo.fill("")
        if busqueda:
            campo.press_sequentially(busqueda, delay=40)
        opciones = self.page.locator("mat-option").locator("visible=true")
        try:
            opciones.first.wait_for(state="visible", timeout=8000)
        except PWTimeout:
            self.fallar(f"'{etiqueta}': el portal no mostró opciones para '{busqueda}'.", captura)
        textos = [t.strip() for t in opciones.all_inner_texts()]
        for i, texto in enumerate(textos):
            if coincide(texto):
                opciones.nth(i).click()
                return texto
        self.fallar(f"'{etiqueta}': ninguna opción coincide con '{busqueda}'. Opciones: {textos}", captura)

    def poner_fecha(self, fecha):
        """El input es de solo lectura: se navega el calendario hasta el mes y se elige el día."""
        self.page.get_by_role("button", name="Open calendar").click()
        periodo = self.page.locator(".mat-calendar-period-button")
        periodo.wait_for(state="visible", timeout=8000)
        texto = _normaliza(periodo.inner_text()).strip()  # p. ej. "oct 2026"
        mes_actual = [m[:3] for m in MESES].index(texto[:3]) + 1
        atras = (int(texto[-4:]) * 12 + mes_actual) - (fecha.year * 12 + fecha.month)
        if atras < 0:
            self.fallar(f"La fecha {fecha:%d/%m/%Y} está en el futuro.", "ticket_error")
        for _ in range(atras):
            self.page.locator(".mat-calendar-previous-button").click()
        dia = self.page.locator(
            f"[aria-label='{fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}']")
        if not dia.count() or "mat-calendar-body-disabled" in (dia.first.get_attribute("class") or ""):
            self.fallar(f"El calendario del portal no permite elegir {fecha:%d/%m/%Y} "
                        "(¿ya pasó el mes de la compra?).", "ticket_error")
        dia.first.click()
        esperar(self.page, 300)
        return self.campo("Fecha").input_value().strip()

    # ---------------------------------------------------------------- pasos

    def pantalla_ticket_heb(self, sol):
        self.elegir_autocompletar("Sucursal", sol.sucursal,
                                  lambda t: t.startswith(f"({sol.sucursal})"), "02_sucursal_error")
        self.escribir("Ticket", sol.ticket)
        fecha_portal = self.poner_fecha(sol.fecha)
        self.escribir("Venta", f"{sol.total:.2f}")
        self.resumen["fecha_portal"] = fecha_portal
        self.ui.captura(self.page, "02_ticket_llenado")
        self.revisar_captcha("ticket")

        self.page.locator("button:has-text('Agregar ticket')").click()
        esperar(self.page, 1000)
        fila = self.page.locator("table").get_by_text(sol.ticket, exact=True)
        try:
            fila.first.wait_for(state="visible", timeout=8000)
        except PWTimeout:
            msgs = self.mensajes_error()
            self.fallar("El portal no agregó el ticket a la lista"
                        + (f": {' '.join(msgs)}" if msgs else "."), "03_agregar_error")
        # "Continuar" valida el ticket y pasa a datos fiscales; no factura.
        self.page.locator("button:has-text('Continuar')").click()
        try:
            self.page.wait_for_url("**/customer-tax-data**", timeout=30000)
        except PWTimeout:
            msgs = self.mensajes_error()
            self.fallar("HEB no aceptó el ticket"
                        + (f": {' '.join(msgs)}" if msgs else " (no avanzó a datos fiscales)."),
                        "03_continuar_error")
        esperar(self.page, 800)
        self.ui.evento("Ticket aceptado por HEB; pasa a datos fiscales.")

    def llenar_datos_fiscales(self, sol):
        self.revisar_captcha("datos_fiscales")
        self.escribir("RFC", self.datos.rfc)
        self.escribir("Nombre \\ Razón social", self.datos.razon_social)
        self.escribir("Código Postal", self.datos.cp)
        self.escribir("Correo electrónico", self.datos.email)
        regimen = _normaliza(self.datos.regimen_texto)
        self.elegir_autocompletar("Régimen fiscal", self.datos.regimen_texto,
                                  lambda t: regimen in _normaliza(t), "04_regimen_error")
        self.elegir_autocompletar("USO DE CFDI", "", lambda t: t.startswith(f"{sol.uso_cfdi} -"),
                                  "04_uso_error")

    def leer_datos_fiscales_heb(self):
        etiquetas = {"RFC": "RFC", "Razón social": "Nombre \\ Razón social", "Código postal": "Código Postal",
                     "Régimen fiscal": "Régimen fiscal", "Correo": "Correo electrónico",
                     "Uso CFDI": "USO DE CFDI"}
        return {k: self.campo(e).input_value().strip() for k, e in etiquetas.items()
                if self.campo(e).count()}

    def confirmar_y_facturar(self, sol):
        fiscales = self.leer_datos_fiscales_heb()
        self.resumen["datos_fiscales_portal"] = fiscales
        self.resumen["captura_datos_fiscales"] = self.ui.captura(self.page, "05_listo_para_facturar")
        # El portal habilita "Generar factura" aunque falten campos: se valida aquí.
        vacios = [k for k in ("RFC", "Razón social", "Código postal", "Régimen fiscal", "Correo", "Uso CFDI")
                  if not fiscales.get(k)]
        if vacios:
            self.fallar(f"Faltan datos fiscales en el portal: {vacios}.", "05_datos_incompletos")
        if not fiscales["Uso CFDI"].startswith(f"{sol.uso_cfdi} -"):
            self.fallar(f"El portal tiene uso CFDI '{fiscales['Uso CFDI']}', distinto de {sol.uso_cfdi}.",
                        "05_uso_distinto")

        r = self.ui.preguntar(
            "confirmar_facturar",
            "Ticket aceptado por HEB. ¿Facturar? El clic en 'Generar factura' emite la factura "
            "DEFINITIVAMENTE; cada ticket solo se factura una vez.",
            ["facturar", "cancelar"],
            captura=self.resumen["captura_datos_fiscales"],
            datos={**self.resumen, "mensajes_del_portal": list(self.mensajes_portal)},
        )
        if r != "facturar":
            raise FlujoCancelado("Cancelado por el usuario antes de facturar.")

        self.page.locator("button:has-text('Generar factura')").click()
        self.ui.marcar_facturado()  # desde aquí no se puede deshacer
        self.ui.evento("Se dio clic en 'Generar factura' (HEB).")
        try:
            self.page.wait_for_url("**/invoice-confirm**", timeout=90000)
        except PWTimeout:
            msgs = self.mensajes_error()
            self.fallar("HEB no confirmó la factura" + (f": {' '.join(msgs)}" if msgs else "."),
                        "06_generar_error")
        esperar(self.page, 1000)
        self.ui.captura(self.page, "06_resultado_final")
        texto_final = self.texto_visible(1200)
        if "generada correctamente" not in texto_final.lower():
            self.fallar("La pantalla final de HEB no confirma la factura.", "06_resultado_dudoso")
        resultado = self.page.locator("table:has(th:has-text('Serie-Folio')) tbody tr").first
        fila = [c.strip() for c in resultado.locator("td").all_inner_texts()]
        # Columnas: [ícono, Serie-Folio, Tienda, Ticket(s), Estado, Mensaje]
        return {"texto_final": texto_final,
                "serie_folio": fila[1] if len(fila) > 1 else "",
                "estado": fila[4] if len(fila) > 4 else "",
                "correo": fiscales["Correo"]}

    # ---------------------------------------------------------------- flujo

    def ejecutar(self, url, sol):
        self.resumen = {"comercio": "HEB", "sucursal": sol.sucursal, "ticket": sol.ticket,
                        "fecha": sol.fecha.strftime("%d/%m/%Y"), "total": f"{sol.total:.2f}",
                        "rfc": self.datos.rfc, "razon_social": self.datos.razon_social,
                        "uso_cfdi": sol.uso_cfdi, "entrega": "email"}
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        esperar(self.page, 1000)
        self.cerrar_aviso()
        self.ui.captura(self.page, "01_formulario")
        self.pantalla_ticket_heb(sol)
        self.llenar_datos_fiscales(sol)
        return self.confirmar_y_facturar(sol)
