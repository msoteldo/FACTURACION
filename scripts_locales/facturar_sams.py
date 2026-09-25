"""Prueba de facturación de un ticket de Sam's/Walmart México.

Instalación (una vez):
    pip install playwright python-dotenv
    playwright install chromium

Datos fiscales: crea un archivo .env junto a este script (NO lo subas a ningún lado):
    RFC=TU_RFC
    CP=TU_CODIGO_POSTAL
    RAZON_SOCIAL=TU_RAZON_SOCIAL
    CALLE=TU_CALLE
    NUM_EXT=TU_NUMERO_EXTERIOR
    NUM_INT=TU_NUMERO_INTERIOR      (opcional)
    REFERENCIA=TU_REFERENCIA        (opcional)
    ESTADO=TU_ESTADO
    MUNICIPIO=TU_MUNICIPIO
    COLONIA=TU_COLONIA
    EMAIL=TU_CORREO

Nota: Régimen Fiscal y Uso de CFDI los eliges tú mismo en la ventana del
navegador cuando el script se detenga a pedírtelo (son selects; no conozco
los valores exactos que acepta el portal).

Uso:
    python facturar_sams.py --inspect                      # solo ver el portal y guardar capturas
    python facturar_sams.py --tc 99064... --tr 03388       # intento real (te pide confirmar antes de enviar)

El navegador se abre visible. Si aparece un captcha lo resuelves tú a mano;
el script NO intenta saltarlo.
"""
import argparse
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

URL = "https://facturacion-clientes.walmart.com/ticket"
OUT = Path("salida")
OUT.mkdir(exist_ok=True)


def dump(page, nombre):
    """Guarda captura y lista los campos/botones de la pantalla (sin imprimir valores)."""
    page.screenshot(path=str(OUT / f"{nombre}.png"), full_page=True)
    print(f"\n--- {nombre}: {page.url}")
    for el in page.query_selector_all("input, select, textarea, button"):
        info = el.evaluate(
            """e => [e.tagName, e.type || '', e.name || '', e.id || '',
                     e.placeholder || '', e.getAttribute('aria-label') || '',
                     e.tagName === 'BUTTON' ? (e.innerText || '').slice(0, 40) : ''].join(' | ')"""
        )
        print(info)
        if el.evaluate("e => e.tagName") == "SELECT":
            opciones = el.evaluate(
                "e => Array.from(e.options).map(o => o.value + ' => ' + o.text).join(' || ')"
            )
            print(f"    opciones: {opciones}")


CAMPOS = {
    "MEMBRESIA_O_RFC": "membershipOrRFC",
    "CP": "postalCode",
    "TC": "ticketNumber",
    "TR": "transactionNumber",
}

# Campos de la pantalla de dirección fiscal (/address). El "name" es literalmente
# la etiqueta visible, tal como reportó el modo --inspect.
CAMPOS_DIRECCION = {
    "RFC": "rfc",
    "RAZON_SOCIAL": "Razón Social",
    "CALLE": "Calle",
    "NUM_EXT": "Número exterior",
    "NUM_INT": "Número interior",
    "REFERENCIA": "Referencia",
    "ESTADO": "Estado",
    "MUNICIPIO": "Municipio/Delegación",
    "COLONIA": "Colonia",
    "CP_DIRECCION": "Código Postal",
    "EMAIL": "email",
}


def llenar_por_name(page, name, valor, verbose_label=None):
    loc = page.locator(f"[name='{name}']")
    n = loc.count()
    if n == 0:
        return False
    if n > 1:
        print(f"    [aviso] hay {n} campos con name='{name}' en la página; usando el primero visible")
        visibles = loc.locator("visible=true")
        loc = visibles if visibles.count() else loc
    el = loc.first
    etiqueta = verbose_label or name

    # Algunos campos (p. ej. RFC en /address) ya vienen prellenados por el portal
    # y bloqueados; no hay que escribirles, solo confirmar que coincidan.
    if el.is_disabled():
        actual = el.input_value()
        if actual == valor:
            print(f"    '{etiqueta}' ya viene prellenado y bloqueado por el portal, coincide: '{actual}'")
            return True
        print(f"    [ALERTA] '{etiqueta}' está bloqueado por el portal con '{actual}', "
              f"distinto de lo esperado '{valor}'")
        return False

    try:
        el.fill(valor, timeout=5000)
    except Exception as e:
        print(f"    [ALERTA] no se pudo escribir en '{etiqueta}': {type(e).__name__}")
        return False

    quedo = el.input_value()
    if quedo != valor:
        print(f"    [ALERTA] '{etiqueta}': escribí '{valor}' pero el campo quedó con '{quedo}'")
        return False
    return True


def opciones_select(page, name):
    loc = page.locator(f"[name='{name}']")
    if not loc.count():
        return []
    return loc.first.evaluate(
        "e => Array.from(e.options).map(o => ({value: o.value, text: o.text}))"
    )


def elegir_opcion_por_texto(page, name, contiene, etiqueta=None):
    """Selecciona en un <select> la primera opción cuyo texto contenga 'contiene' (sin distinguir mayúsculas/acentos)."""
    import unicodedata

    def normaliza(s):
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        return s.lower()

    objetivo = normaliza(contiene)
    ops = opciones_select(page, name)
    for o in ops:
        if objetivo in normaliza(o["text"]):
            page.locator(f"[name='{name}']").first.select_option(value=o["value"])
            print(f"    '{etiqueta or name}' = {o['text']} (value={o['value']})")
            return True
    print(f"    [ALERTA] no encontré en '{etiqueta or name}' una opción que contenga '{contiene}'.")
    print(f"    Opciones disponibles: {ops}")
    return False


def cerrar_popup_inicial(page):
    """Cierra el aviso emergente que el portal puede mostrar en varias pantallas."""
    boton = page.locator("#popup_btn_accept")
    if boton.count() and boton.first.is_visible():
        boton.first.click()
        page.wait_for_load_state("networkidle")
        return True
    return False


FORMAS_PAGO = {
    "1": ("04", "Tarjeta de crédito"),
    "2": ("28", "Tarjeta de débito"),
    "3": ("05", "Monedero electrónico"),
}


def elegir_forma_pago(page):
    """Pregunta en la terminal cómo se pagó el ticket y lo selecciona en el portal."""
    print("\n¿Con qué se pagó este ticket?")
    print("  1) Tarjeta de crédito (04)")
    print("  2) Tarjeta de débito (28)")
    print("  3) Monedero electrónico (05)")
    eleccion = input("Elige 1, 2 o 3: ").strip()
    if eleccion not in FORMAS_PAGO:
        print("Opción no válida; selecciónala tú en la ventana.")
        return False
    valor, nombre = FORMAS_PAGO[eleccion]
    sel = page.locator("select")
    if not sel.count():
        print("[ALERTA] no encontré el select de forma de pago; selecciónalo tú en la ventana.")
        return False
    try:
        sel.first.select_option(value=valor)
    except Exception as e:
        print(f"[ALERTA] no pude seleccionar la forma de pago: {type(e).__name__}")
        return False
    print(f"    Forma de pago = {nombre} (value={valor})")
    return True


def completar_invoice_selection(page):
    """Pantalla final: elegir cómo recibir la factura y confirmar el envío definitivo."""
    print("\nLlegamos a la pantalla de entrega de la factura.")
    print("¿Cómo quieres recibirla?")
    print("  1) Por correo electrónico")
    print("  2) Descargar PDF/XML directo")
    eleccion = input("Elige 1 o 2: ").strip()

    radio_id = "#method_email_radio" if eleccion == "1" else "#method_pdf_radio"
    radio = page.locator(radio_id)
    if radio.count():
        radio.first.check()
    else:
        print(f"[ALERTA] no encontré el radio '{radio_id}'; selecciónalo tú en la ventana.")

    if eleccion == "1":
        alterno = input(
            "¿Correo alternativo para enviarla? (Enter para dejar el que ya diste antes): "
        ).strip()
        if alterno:
            campo = page.locator("[name='invoice_form_email_alt_input']")
            if campo.count():
                campo.first.fill(alterno)
            else:
                print("[ALERTA] no encontré el campo de correo alternativo.")

    dump(page, "11_entrega_lista")

    if input(
        "\n¿Dar clic en 'Facturar' para GENERAR DEFINITIVAMENTE la factura?"
        " Esto ya no se puede deshacer (s/n): "
    ).strip().lower() != "s":
        print("Cancelado por el usuario; no se envió la factura.")
        return

    boton = page.locator("#invoice_form_btn_submit")
    if not boton.count():
        print("[ALERTA] no encontré el botón 'Facturar'.")
        return

    boton.first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    dump(page, "12_resultado_facturacion")

    for i in range(1, 4):
        if not manejar_modal(page, f"13_confirmacion_final_{i}"):
            break
    page.wait_for_timeout(2000)
    dump(page, "14_resultado_final")
    print(f"\nRevisa la carpeta '{OUT}' por si el portal descargó el XML/PDF de la factura.")


def manejar_modal(page, paso):
    """Si aparece el modal dinámico, muestra su texto y pide confirmación antes de continuar.
    Devuelve True si hizo clic en Continuar, False si no había modal o el usuario dijo que no."""
    primario = page.locator("#dynamic_modal_primary_btn")
    if not (primario.count() and primario.first.is_visible()):
        return False
    texto = primario.first.evaluate(
        """e => {
            const m = e.closest('[role=dialog], .modal, [class*=modal]');
            return ((m || e.parentElement.parentElement || e.parentElement).innerText || '').trim();
        }"""
    )
    page.screenshot(path=str(OUT / f"{paso}_modal.png"), full_page=True)
    print(f"\n--- {paso}: el portal muestra este mensaje ---")
    print(texto if texto else "(no pude leer el texto del modal; revisa la ventana)")
    print("-----------------------------------------------")
    resp = input("¿Dar clic en 'Continuar' en ese mensaje? (s = Continuar / n = Cerrar): ").strip().lower()
    if resp == "s":
        primario.first.click()
    else:
        secundario = page.locator("#dynamic_modal_secondary_btn")
        if secundario.count() and secundario.first.is_visible():
            secundario.first.click()
        return False
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    return True


def hay_captcha(page):
    if page.locator("iframe[src*='captcha'], iframe[title*='captcha' i]").count():
        return True
    return "captcha" in page.content().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tc")
    ap.add_argument("--tr")
    ap.add_argument("--inspect", action="store_true", help="solo inspeccionar el portal")
    a = ap.parse_args()

    rfc, cp = os.environ.get("RFC"), os.environ.get("CP")
    direccion = {
        "RFC": rfc,  # mismo RFC fiscal
        "RAZON_SOCIAL": os.environ.get("RAZON_SOCIAL", ""),
        "CALLE": os.environ.get("CALLE", ""),
        "NUM_EXT": os.environ.get("NUM_EXT", ""),
        "NUM_INT": os.environ.get("NUM_INT", ""),
        "REFERENCIA": os.environ.get("REFERENCIA", ""),
        "ESTADO": os.environ.get("ESTADO", ""),
        "MUNICIPIO": os.environ.get("MUNICIPIO", ""),
        "COLONIA": os.environ.get("COLONIA", ""),
        "CP_DIRECCION": cp,
        "EMAIL": os.environ.get("EMAIL", ""),
    }
    if not a.inspect and not (a.tc and a.tr and rfc and cp):
        sys.exit("Faltan --tc, --tr, o RFC/CP en el archivo .env")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        def guardar_descarga(d):
            destino = OUT / d.suggested_filename
            d.save_as(str(destino))
            print(f"\n[descarga] guardado: {destino}")

        page.on("download", guardar_descarga)
        page.goto(URL, wait_until="networkidle")
        dump(page, "1_inicio")

        cerrar_popup_inicial(page)

        # La app es un SPA; ir directo a /ticket a veces no basta.
        # Si el botón "Obtener factura" está visible, hay que darle clic.
        obtener = page.locator("#obtener_factura_button")
        if obtener.count():
            obtener.first.click()
            page.wait_for_load_state("networkidle")

        # Asegurar que estamos en la pestaña "Facturar" (no "Consulta")
        tab = page.locator("#invoice_tab_facturar")
        if tab.count():
            tab.first.click()
            page.wait_for_load_state("networkidle")
        dump(page, "2_formulario")

        if a.inspect:
            input("\nModo inspección terminado. Enter para cerrar...")
            browser.close()
            return

        resultados = {
            "MEMBRESIA_O_RFC": llenar_por_name(page, CAMPOS["MEMBRESIA_O_RFC"], rfc),
            "CP": llenar_por_name(page, CAMPOS["CP"], cp),
            "TC": llenar_por_name(page, CAMPOS["TC"], a.tc),
            "TR": llenar_por_name(page, CAMPOS["TR"], a.tr),
        }
        faltan = [k for k, ok in resultados.items() if not ok]
        if faltan:
            print(f"\nNo encontré los campos: {faltan}. Revisa salida/2_formulario.png y la lista de arriba.")
            input("Enter para cerrar...")
            browser.close()
            return

        if hay_captcha(page):
            input("\nHay un captcha. Resuélvelo en la ventana y pulsa Enter...")

        if input("\n¿Enviar el formulario ahora? (s/n): ").strip().lower() != "s":
            browser.close()
            return

        cont = page.locator("#form_btn_accept")
        if not cont.count():
            print("No encontré el botón 'Continuar'.")
        else:
            cont.first.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)  # la app es SPA; dar tiempo a que renderice la pantalla nueva
            dump(page, "3_despues_continuar")

        if "/address" in page.url:
            cerrar_popup_inicial(page)  # esta pantalla también puede mostrar un aviso emergente
            print("\nLlegamos a la pantalla de dirección fiscal. Llenando los campos de texto...")
            faltan_dir = []
            for clave, name in CAMPOS_DIRECCION.items():
                valor = direccion.get(clave, "")
                if not valor:
                    continue  # campo opcional sin valor en .env, se deja como está
                if not llenar_por_name(page, name, valor, verbose_label=clave):
                    faltan_dir.append(clave)
            if faltan_dir:
                print(f"No encontré o no se sostuvieron estos campos: {faltan_dir}")

            # Segunda pasada: el portal a veces recalcula/sobreescribe campos de forma asíncrona
            # (p. ej. Razón Social a partir del RFC) un momento después de escribirlos.
            page.wait_for_timeout(1500)
            print("\nVerificación final de los campos (por si el portal los sobrescribió después):")
            for clave, name in CAMPOS_DIRECCION.items():
                valor_esperado = direccion.get(clave, "")
                if not valor_esperado:
                    continue
                loc = page.locator(f"[name='{name}']")
                if loc.count():
                    actual = loc.first.input_value()
                    marca = "OK" if actual == valor_esperado else "DIFERENTE"
                    print(f"  {clave}: esperado='{valor_esperado}' actual='{actual}' [{marca}]")

            print("\nRégimen Fiscal seleccionado: buscando 'General de Ley Personas Morales'...")
            elegir_opcion_por_texto(page, "Régimen Fiscal", "General de Ley Personas Morales",
                                     etiqueta="Régimen Fiscal")
            page.wait_for_timeout(1200)  # dar tiempo a que el portal recalcule las opciones de Uso de CFDI

            print("\n¿Este ticket es para reventa (mercancía de la barra/snack) o gasto/consumo del club?")
            print("  1) Adquisición de mercancías (G01)")
            print("  2) Gastos en general (G03)")
            eleccion = input("Elige 1 o 2: ").strip()
            busqueda_uso = "adquisici" if eleccion == "1" else "general"
            if not elegir_opcion_por_texto(page, "Uso Factura", busqueda_uso, etiqueta="Uso Factura"):
                print("No pude elegirlo automáticamente; selecciónalo tú en la ventana antes de continuar.")

            dump(page, "4_direccion_llenada")

            input("\nRevisa Régimen Fiscal y Uso de CFDI en la ventana (corrígelos ahí si hace falta)."
                  " Cuando estén listos, pulsa Enter aquí...")
            dump(page, "5_antes_de_aceptar_final")

            if hay_captcha(page):
                input("\nHay un captcha. Resuélvelo en la ventana y pulsa Enter...")

            if input("\n¿Dar clic en 'Aceptar' para enviar la factura? (s/n): ").strip().lower() == "s":
                aceptar_final = page.locator("#form_btn_accept")
                if aceptar_final.count():
                    aceptar_final.first.click()
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(2000)
                    dump(page, "6_resultado_final")

                    # El portal puede mostrar uno o varios mensajes de confirmación encadenados.
                    for i in range(1, 4):
                        if not manejar_modal(page, f"7_confirmacion_{i}"):
                            break
                    page.wait_for_timeout(2000)
                    dump(page, "8_despues_de_confirmar")

                    if "/payment" in page.url:
                        elegir_forma_pago(page)
                        dump(page, "9_pago_elegido")
                        if input("\n¿Dar clic en 'Continuar' para generar la factura? (s/n): ").strip().lower() == "s":
                            cont_pago = page.locator("#form_btn_accept")
                            if cont_pago.count():
                                cont_pago.first.click()
                                page.wait_for_load_state("networkidle")
                                page.wait_for_timeout(2500)
                                dump(page, "10_despues_de_pago")

                                if "/invoiceSelection" in page.url:
                                    completar_invoice_selection(page)
                                else:
                                    # Respaldo genérico por si el portal muestra un modal en vez de esta pantalla.
                                    for i in range(1, 4):
                                        if not manejar_modal(page, f"11_confirmacion_{i}"):
                                            break
                                    page.wait_for_timeout(2000)
                                    dump(page, "12_resultado")
                                    print(f"\nRevisa la carpeta '{OUT}' por si el portal descargó XML/PDF.")
                            else:
                                print("No encontré el botón 'Continuar' de la pantalla de pago.")
                else:
                    print("No encontré el botón 'Aceptar' final.")

        input("\nRevisa la ventana. Enter para cerrar...")
        browser.close()


if __name__ == "__main__":
    main()