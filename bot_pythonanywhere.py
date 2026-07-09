"""Telegram Finance Bot v2 — PythonAnywhere Edition
Flask WSGI + python-telegram-bot + Supabase
"""
import asyncio, json, logging, os
from datetime import datetime, timedelta

from flask import Flask, request
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application

from finance_db import get_db as finance_get_db, init_db as finance_init_db
from handlers_registry import register_handlers
import finance_reports
from finance_analytics import _build_anomalies, _build_financial_snapshot, _format_panel_text
from finance_state import get_system_state, save_system_state

from commands import (
    cmd_agregar_alerta, cmd_agregar_recurrente, cmd_agregaringresorecurrente,
    cmd_ahorro,     cmd_alertas, cmd_anomalias, cmd_aportarmeta, cmd_borrar_alerta,
    cmd_borrar_cuenta, cmd_borrar_recurrente, cmd_borrarfactura, cmd_burnrate, cmd_buscar, cmd_cancel,
    cmd_comparar, cmd_consejo, cmd_cuentas, cmd_deshacer, cmd_exportar, cmd_factura, cmd_facturas, cmd_fantasmas, cmd_forecast, cmd_gasto,
    cmd_gasto_rapido, cmd_help, cmd_ingreso, cmd_ingresorecurrente, cmd_menu, cmd_metas,
    cmd_nueva_cuenta, cmd_nuevameta, cmd_panel, cmd_patrimonio, cmd_presupuesto,
    cmd_presupuestoset, cmd_proyeccion, cmd_recurrente, cmd_redondeo, cmd_redondeo_cuenta,
    cmd_redondeo_toggle, cmd_regla, cmd_reset, cmd_resumendiario, cmd_start, cmd_stats,
    cmd_sugerircategoria, cmd_tags, cmd_tendencia, cmd_traspaso,
)
from callbacks import (
    handle_budget_callback, handle_callback, handle_flow_callback,
    handle_menu_callback, handle_resumen_callback,
)
from flows import handle_text

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not set!")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
WEBHOOK_PATH = f"/{TOKEN}"


async def init_db():
    return await finance_init_db(SUPABASE_URL, SUPABASE_KEY)


async def get_db():
    return await finance_get_db(SUPABASE_URL, SUPABASE_KEY)


# ── FLASK APP ────────────────────────────────────────────────────────
app = Flask(__name__)
application = app
ptb_app = None
_event_loop = None
_ptb_app_lock = asyncio.Lock()


def get_event_loop():
    global _event_loop
    if _event_loop is None:
        _event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_event_loop)
    return _event_loop


async def _create_ptb_app():
    global ptb_app
    if ptb_app is not None:
        return ptb_app

    async with _ptb_app_lock:
        if ptb_app is not None:
            return ptb_app
        application = Application.builder().token(TOKEN).build()
        application.bot_data["db"] = await init_db()
        register_handlers(application, {
            "cmd_start": cmd_start,
            "cmd_help": cmd_help,
            "cmd_menu": cmd_menu,
            "cmd_cancel": cmd_cancel,
            "cmd_cuentas": cmd_cuentas,
            "cmd_nueva_cuenta": cmd_nueva_cuenta,
            "cmd_borrar_cuenta": cmd_borrar_cuenta,
            "cmd_gasto": cmd_gasto,
            "cmd_gasto_rapido": cmd_gasto_rapido,
            "cmd_ingreso": cmd_ingreso,
            "cmd_traspaso": cmd_traspaso,
            "cmd_deshacer": cmd_deshacer,
            "cmd_redondeo": cmd_redondeo,
            "cmd_redondeo_toggle": cmd_redondeo_toggle,
            "cmd_redondeo_cuenta": cmd_redondeo_cuenta,
            "cmd_recurrente": cmd_recurrente,
            "cmd_agregar_recurrente": cmd_agregar_recurrente,
            "cmd_borrar_recurrente": cmd_borrar_recurrente,
            "cmd_resumen": finance_reports.cmd_resumen,
            "cmd_stats": finance_reports.cmd_stats,
            "cmd_tendencia": finance_reports.cmd_tendencia,
            "cmd_panel": finance_reports.cmd_panel,
            "cmd_patrimonio": cmd_patrimonio,
            "cmd_burnrate": cmd_burnrate,
            "cmd_comparar": cmd_comparar,
            "cmd_ahorro": cmd_ahorro,
            "cmd_consejo": cmd_consejo,
            "cmd_regla": cmd_regla,
            "cmd_proyeccion": cmd_proyeccion,
            "cmd_fantasmas": cmd_fantasmas,
            "cmd_resumendiario": cmd_resumendiario,
            "cmd_factura": cmd_factura,
            "cmd_facturas": cmd_facturas,
            "cmd_borrarfactura": cmd_borrarfactura,
            "cmd_forecast": finance_reports.cmd_forecast,
            "cmd_anomalias": finance_reports.cmd_anomalias,
            "cmd_tags": finance_reports.cmd_tags,
            "cmd_sugerircategoria": finance_reports.cmd_sugerircategoria,
            "cmd_exportar": finance_reports.cmd_exportar,
            "cmd_alertas": cmd_alertas,
            "cmd_agregar_alerta": cmd_agregar_alerta,
            "cmd_borrar_alerta": cmd_borrar_alerta,
            "cmd_reset": cmd_reset,
            "cmd_presupuesto": cmd_presupuesto,
            "cmd_presupuestoset": cmd_presupuestoset,
            "cmd_buscar": cmd_buscar,
            "cmd_metas": cmd_metas,
            "cmd_nuevameta": cmd_nuevameta,
            "cmd_aportarmeta": cmd_aportarmeta,
            "cmd_agregaringresorecurrente": cmd_agregaringresorecurrente,
            "cmd_ingresorecurrente": cmd_ingresorecurrente,
            "handle_menu_callback": handle_menu_callback,
            "handle_resumen_callback": handle_resumen_callback,
            "handle_budget_callback": handle_budget_callback,
            "handle_callback": handle_callback,
            "handle_flow_callback": handle_flow_callback,
            "handle_text": handle_text,
        })

        async def check_recurring_reminders(ctx):
            db = application.bot_data["db"]
            now = datetime.now()
            target = now + timedelta(days=1)
            c = await db.execute(
                "SELECT r.*,u.telegram_id FROM recurring_expenses r JOIN users u ON r.user_id=u.id WHERE r.next_date<=?", (target.isoformat(),)
            )
            for rec in await c.fetchall():
                try:
                    await ctx.bot.send_message(
                        chat_id=rec["telegram_id"],
                        text=f"📅 <b>Recordatorio de pago</b>\n\n{rec['name']}: €{'%.2f' % rec['amount']} ({rec['frequency']})",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    logger.exception("No se pudo enviar recordatorio recurrente")

        async def maybe_send_weekly_panel(ctx):
            db = application.bot_data["db"]
            now = datetime.now()
            if now.weekday() != 0:
                return
            meta = await get_system_state(db)
            meta_data = json.loads(meta["data"]) if meta and meta["data"] else {}
            today = now.date().isoformat()
            if meta_data.get("weekly_panel_last_sent") == today:
                return
            users = await db._select_rows("users", columns="telegram_id")
            for user in users:
                try:
                    uid = user.get("telegram_id")
                    if uid is None:
                        continue
                    snapshot = await _build_financial_snapshot(db, uid)
                    anomalies = await _build_anomalies(db, uid)
                    await ctx.bot.send_message(
                        chat_id=uid, text=_format_panel_text(snapshot, anomalies), parse_mode=ParseMode.HTML
                    )
                except Exception:
                    logger.exception("No se pudo enviar el panel semanal")
            meta_data["weekly_panel_last_sent"] = today
            await save_system_state(db, "bot_meta", meta_data)

        async def send_daily_summaries(ctx):
            db = application.bot_data["db"]
            rows = await (await db.execute("SELECT telegram_id FROM users WHERE daily_summary_enabled=true")).fetchall()
            for user in rows:
                try:
                    tid = user["telegram_id"]
                    c = await db.execute("SELECT type,amount FROM transactions WHERE date>=?", (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),))
                    tx_rows = await c.fetchall()
                    income = sum(r["amount"] for r in tx_rows if r["type"] == "INGRESO")
                    expense = sum(r["amount"] for r in tx_rows if r["type"] == "GASTO")
                    await ctx.bot.send_message(
                        chat_id=tid,
                        text=f"☀️ <b>Resumen de hoy</b>\n\n💰 Ingresos: <b>€{income:.2f}</b>\n💸 Gastos: <b>€{expense:.2f}</b>\n📊 Balance: <b>€{income-expense:+.2f}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    logger.exception("No se pudo enviar resumen diario")

        async def check_bill_reminders(ctx):
            db = application.bot_data["db"]
            today = datetime.now().day
            rows = await (await db.execute("SELECT * FROM bill_reminders WHERE day_of_month=? AND enabled=true", (today,))).fetchall()
            for bill in rows:
                try:
                    user = await (await db.execute("SELECT telegram_id FROM users WHERE id=?", (bill["user_id"],))).fetchone()
                    if user:
                        await ctx.bot.send_message(
                            chat_id=user["telegram_id"],
                            text=f"🧾 <b>Recordatorio de factura</b>\n\n{bill['name']}: €{'%.2f' % bill['amount']}\n📅 Hoy es día {today}",
                            parse_mode=ParseMode.HTML,
                        )
                except Exception:
                    logger.exception("No se pudo enviar recordatorio de factura")

        application.job_queue.run_repeating(check_recurring_reminders, interval=3600, first=10)
        application.job_queue.run_repeating(maybe_send_weekly_panel, interval=3600, first=60)
        application.job_queue.run_repeating(send_daily_summaries, interval=3600, first=30)
        application.job_queue.run_repeating(check_bill_reminders, interval=3600, first=120)

        ptb_app = application
        return application


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    loop = get_event_loop()
    if TELEGRAM_WEBHOOK_SECRET:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if received_secret != TELEGRAM_WEBHOOK_SECRET:
            logger.warning("Rejected webhook with invalid Telegram secret token")
            return "forbidden", 403
    try:
        data = request.get_json(force=True)
    except Exception as e:
        logger.exception("Failed to parse webhook JSON")
        return "bad request", 400

    async def process():
        app_ptb = await _create_ptb_app()
        update = Update.de_json(data, app_ptb.bot)
        await app_ptb.process_update(update)
        return "OK"

    try:
        loop.run_until_complete(process())
    except Exception as e:
        logger.exception("Error processing webhook update")
        return "error", 500
    return "OK", 200


@app.route("/")
def index():
    return "Finance Bot OK", 200
