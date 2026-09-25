"""Bot de Telegram con una Bot API simulada, de punta a punta contra el portal simulado."""
import dataclasses
import itertools
import json
import time

from fastapi.testclient import TestClient

from app import extraccion, main
from app.telegram_bot import BotFacturacion

CHAT = 111
TC = "9906477777777777777777"


class APIFalsa:
    def __init__(self):
        self.llamadas = []
        self._ids = itertools.count(1)

    def llamar(self, metodo, files=None, **params):
        if "reply_markup" in params and isinstance(params["reply_markup"], str):
            params["reply_markup"] = json.loads(params["reply_markup"])
        self.llamadas.append((metodo, params, bool(files)))
        return {"message_id": next(self._ids)}

    def descargar(self, file_id):
        return b"foto"

    def buscar(self, condicion, timeout=60):
        fin = time.time() + timeout
        while time.time() < fin:
            for metodo, params, _ in list(self.llamadas):
                if condicion(metodo, params):
                    return params
            time.sleep(0.3)
        raise AssertionError(f"no llegó el mensaje esperado; llamadas: {[m for m, *_ in self.llamadas]}")


def texto(params):
    return params.get("text") or params.get("caption") or ""


def botones(params):
    return [b["callback_data"] for fila in params.get("reply_markup", {}).get("inline_keyboard", [])
            for b in fila]


def nuevo_bot():
    ajustes = dataclasses.replace(main.ajustes, telegram_token="x", telegram_secreto="s",
                                  telegram_chats=frozenset({CHAT}))
    api = APIFalsa()
    bot = BotFacturacion(ajustes, main.gestor, api=api)
    main.gestor.oyentes.append(bot)
    return bot, api


_updates = itertools.count(1)


def foto(chat=CHAT):
    return {"update_id": next(_updates),
            "message": {"chat": {"id": chat}, "photo": [{"file_id": "chica"}, {"file_id": "grande"}]}}


def boton(data, chat=CHAT):
    return {"update_id": next(_updates),
            "callback_query": {"id": "cb", "data": data,
                               "message": {"chat": {"id": chat}, "message_id": 1}}}


def ticket_leido(monkeypatch, forma="28", tc=TC):
    monkeypatch.setattr(extraccion, "leer_ticket", lambda *a, **k: {
        "comercio": "Sam's Club", "total": 1417.86, "tc_numero_ticket": tc,
        "tr_numero_transaccion": "03388", "forma_pago_sat_sugerida": forma,
        "confianza_tc": "alta", "confianza_tr": "alta", "advertencias": [],
        "requiere_revision": False})


def test_flujo_foto_a_factura(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch)
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert TC in texto(resumen) and "Débito" in texto(resumen)
        assert "¿Qué quieres hacer?" in texto(resumen)
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p))
        uso_g01 = next(b for b in botones(usos) if b.endswith(":G01"))

        # Forma de pago leída del ticket: con elegir G01 ya arranca.
        bot.procesar(boton(uso_g01))
        pregunta = api.buscar(lambda m, p: m == "sendPhoto" and "¿Facturar?" in texto(p))
        assert "débito" in texto(pregunta) and "datos fiscales" in texto(pregunta)  # mensaje del portal
        facturar = next(b for b in botones(pregunta) if b.endswith(":facturar"))

        bot.procesar(boton(facturar))
        fin = api.buscar(lambda m, p: "Factura generada" in texto(p))
        assert fin is not None
        # Los botones usados se quitan para evitar dobles clics.
        assert any(m == "editMessageReplyMarkup" for m, *_ in api.llamadas)
    finally:
        main.gestor.oyentes.remove(bot)


def test_pide_forma_de_pago_si_no_se_leyo_y_se_puede_cancelar(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch, forma=None, tc="9906488888888888888888")
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p))
        bot.procesar(boton(next(b for b in botones(usos) if b.endswith(":G03"))))
        formas = api.buscar(lambda m, p: "¿Con qué se pagó?" in texto(p))
        bot.procesar(boton(next(b for b in botones(formas) if b.endswith(":04"))))
        pregunta = api.buscar(lambda m, p: "¿Facturar?" in texto(p))
        assert "crédito" in texto(pregunta) and "30 s" in texto(pregunta)
        bot.procesar(boton(next(b for b in botones(pregunta) if b.endswith(":cancelar"))))
        assert api.buscar(lambda m, p: "No se facturó nada" in texto(p))
    finally:
        main.gestor.oyentes.remove(bot)


def test_botones_viejos_y_correccion_de_tc(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch)
        bot.procesar(foto())
        viejo = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        api.llamadas.clear()
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/tc 1234567890123"}})
        nuevo = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert "1234567890123" in texto(nuevo)
        # El botón del mensaje anterior ya no sirve.
        bot.procesar(boton(botones(viejo)[0]))
        aviso = api.buscar(lambda m, p: m == "answerCallbackQuery" and p.get("text"))
        assert "ya no está pendiente" in aviso["text"]
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/cancelar"}})
        assert api.buscar(lambda m, p: "descartado" in texto(p))
    finally:
        main.gestor.oyentes.remove(bot)


def test_chat_no_autorizado(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch)
        bot.procesar(foto(chat=999))
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": 999}, "text": "/facturar 1234567890 1"}})
        assert api.llamadas == []  # silencio total
        bot.procesar({"update_id": next(_updates), "message": {"chat": {"id": 999}, "text": "/start"}})
        assert "999" in texto(api.llamadas[-1][1]) and "privado" in texto(api.llamadas[-1][1])
        # Un update repetido (Telegram reintenta) se ignora.
        u = foto()
        bot.procesar(u)
        n = len(api.llamadas)
        bot.procesar(u)
        assert len(api.llamadas) == n
    finally:
        main.gestor.oyentes.remove(bot)


def test_webhook_exige_secreto(monkeypatch):
    bot, api = nuevo_bot()
    main.gestor.oyentes.remove(bot)
    monkeypatch.setattr(main, "bot", bot)
    monkeypatch.setattr(main, "ajustes", bot.ajustes)
    c = TestClient(main.app)
    upd = {"update_id": next(_updates), "message": {"chat": {"id": CHAT}, "text": "/ayuda"}}
    assert c.post("/telegram/webhook", json=upd).status_code == 401
    assert c.post("/telegram/webhook", json=upd,
                  headers={"X-Telegram-Bot-Api-Secret-Token": "otro"}).status_code == 401
    # El secreto crudo tampoco sirve: Telegram manda su SHA-256 (ver BotFacturacion.secreto).
    assert c.post("/telegram/webhook", json=upd,
                  headers={"X-Telegram-Bot-Api-Secret-Token": "s"}).status_code == 401
    r = c.post("/telegram/webhook", json=upd,
               headers={"X-Telegram-Bot-Api-Secret-Token": bot.secreto})
    assert r.status_code == 200
    assert api.buscar(lambda m, p: "Facturación de tickets" in texto(p))


def test_registro_del_webhook():
    bot, api = nuevo_bot()
    main.gestor.oyentes.remove(bot)
    bot.ajustes = dataclasses.replace(bot.ajustes, url_publica="https://ejemplo.onrender.com")
    bot.configurar_webhook()
    metodo, params, _ = api.llamadas[-1]
    assert metodo == "setWebhook"
    assert params["url"] == "https://ejemplo.onrender.com/telegram/webhook"
    # Secreto en formato que Telegram acepta aunque el configurado traiga "+/=".
    assert params["secret_token"] == bot.secreto and bot.secreto.isalnum() and len(bot.secreto) == 64


def test_consultar_no_facturado_y_facturarlo_desde_el_aviso(monkeypatch):
    bot, api = nuevo_bot()
    try:
        # En el portal simulado, un número que empieza con 000 "no se encuentra facturado".
        ticket_leido(monkeypatch, tc="0000000000000000000009")
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":con"))))
        aviso = api.buscar(lambda m, p: "aún no está facturado" in texto(p))
        assert "no se encuentra facturado" in texto(aviso)
        bot.procesar(boton(next(b for b in botones(aviso) if b.endswith(":fac"))))
        usos = api.buscar(lambda m, p: "¿Para qué es la compra?" in texto(p))
        assert any(b.endswith(":G01") for b in botones(usos))
    finally:
        main.gestor.oyentes.remove(bot)


def test_comando_facturar_va_directo_a_uso():
    bot, api = nuevo_bot()
    try:
        bot.procesar({"update_id": next(_updates),
                      "message": {"chat": {"id": CHAT}, "text": "/facturar 9906412341234123412341 03388"}})
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        assert "¿Para qué es la compra?" in texto(resumen)
        assert not any(b.endswith(":con") for b in botones(resumen))
    finally:
        main.gestor.oyentes.remove(bot)


def test_consultar_ticket_facturado_solo_avisa(monkeypatch):
    bot, api = nuevo_bot()
    try:
        ticket_leido(monkeypatch, tc="9906499999999999999999")  # el portal simulado lo encuentra
        bot.procesar(foto())
        resumen = api.buscar(lambda m, p: "Ticket leído" in texto(p))
        bot.procesar(boton(next(b for b in botones(resumen) if b.endswith(":con"))))
        aviso = api.buscar(lambda m, p: "ya está facturado" in texto(p))
        assert "9906499999999999999999" in texto(aviso)
        assert CHAT not in bot.pendientes
    finally:
        main.gestor.oyentes.remove(bot)
