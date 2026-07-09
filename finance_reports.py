import csv
import io
from datetime import datetime

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ChatAction, ParseMode
except Exception:
    class InlineKeyboardButton:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    class ChatAction:
        TYPING = "typing"
        UPLOAD_DOCUMENT = "upload_document"

    class ParseMode:
        HTML = "HTML"

from finance_analytics import _build_anomalies, _build_financial_snapshot, _format_panel_text, bar_chart, get_monthly_tx, predict_expenses, savings_recs, trend_chart, unicode_table
from finance_db import get_db
from finance_notifications import check_alerts
from finance_shared import CATEGORY_MAP, MONTHS_ES, _smart_category_suggestion, end_of_month, h
from finance_state import get_accounts, get_or_create_user, get_roundup, save_session
from finance_ui import _acct_kb, _kb, multi_kb


async def cmd_resumen(update, ctx):
    db = await get_db()
    tid = update.effective_user.id
    uid = await get_or_create_user(db, tid)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    now = datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = end_of_month(now)
    c = await db.execute(
        "SELECT * FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA' ORDER BY date DESC",
        (uid, start.isoformat(), end.isoformat()),
    )
    txs = await c.fetchall()
    if not txs:
        return await update.effective_message.reply_text(
            f"<b>Resumen de {h(MONTHS_ES[now.month])} {h(str(now.year))}</b>\n\nNo hay transacciones este mes.",
            parse_mode=ParseMode.HTML,
        )
    by_cat, ti, te = {}, 0.0, 0.0
    for tx in txs:
        if tx["type"] == "INGRESO":
            ti += tx["amount"]
        elif tx["type"] == "GASTO":
            te += tx["amount"]
            by_cat[tx["category"]] = by_cat.get(tx["category"], 0) + tx["amount"]
    bal, rate = ti - te, (ti - te) / ti * 100 if ti > 0 else 0.0
    msg = (
        f"📊 <b>Resumen de {h(MONTHS_ES[now.month])} {h(str(now.year))}</b>\n\n"
        f"📈 Ingresos: €{h(f'{ti:.2f}')}\n📉 Gastos: €{h(f'{te:.2f}')}\n💵 Balance: €{h(f'{bal:.2f}')}\n📊 Tasa de ahorro: {h(f'{rate:.1f}')}%\n\n"
        f"<b>Gastos por categoria:</b>\n"
    )
    for cat, amt in sorted(by_cat.items(), key=lambda e: e[1], reverse=True):
        pct = (amt / te * 100)
        msg += f"  {h(cat)}: €{h(f'{amt:.2f}')} ({h(f'{pct:.1f}')}%)\n"
    kb_rows = []
    if by_cat:
        kb_rows.append([InlineKeyboardButton("📊 Grafico Categorias", callback_data="resumen_cat_c")])
    kb_rows.append([InlineKeyboardButton("📈 Tendencias 4m", callback_data="resumen_trend_t")])
    kb_rows.append([InlineKeyboardButton("🔮 Predicciones", callback_data="resumen_pred_p")])
    kb_rows.append([InlineKeyboardButton("💡 Recomendaciones", callback_data="resumen_rec_r")])
    kb_rows.append([InlineKeyboardButton("🔔 Alertas", callback_data="resumen_alerts_a")])
    await save_session(db, tid, "resumen_data", {"by_cat": by_cat, "ti": ti, "te": te, "uid": uid, "now_month": MONTHS_ES[now.month], "now_year": str(now.year)})
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb_rows))


async def cmd_stats(update, ctx):
    db = await get_db()
    uid = await get_or_create_user(db, update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    monthly = await get_monthly_tx(db, uid, 6)
    headers = ["Mes", "Ingresos", "Gastos", "Balance", "% Ahorro"]
    rows = []
    for m, d in monthly.items():
        bal = d["income"] - d["expense"]
        rate = (bal / d["income"] * 100) if d["income"] > 0 else 0.0
        rows.append([m, f"€{d['income']:.2f}", f"€{d['expense']:.2f}", f"€{bal:.2f}", f"{rate:.1f}%"])
    tbl = unicode_table(headers, rows)
    await update.effective_message.reply_text(f"📈 <b>Estadisticas ultimos 6 meses</b>\n<pre>{h(tbl)}</pre>", parse_mode=ParseMode.HTML)


async def cmd_tendencia(update, ctx):
    db = await get_db()
    uid = await get_or_create_user(db, update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    monthly = await get_monthly_tx(db, uid, 12)
    ed = [{"month": m, "amount": v["expense"]} for m, v in monthly.items()]
    id_data = [{"month": m, "amount": v["income"]} for m, v in monthly.items()]
    await update.effective_message.reply_text(f"<pre>{h(trend_chart(ed, 'Tendencia de gastos (12 meses)'))}</pre>", parse_mode=ParseMode.HTML)
    await update.effective_message.reply_text(f"<pre>{h(trend_chart(id_data, 'Tendencia de ingresos (12 meses)'))}</pre>", parse_mode=ParseMode.HTML)
    items = list(monthly.items())
    if len(items) >= 2:
        _, pv = items[-2]
        _, lv = items[-1]
        ediff = lv["expense"] - pv["expense"]
        idiff = lv["income"] - pv["income"]
        ep = abs(ediff / pv["expense"] * 100) if pv["expense"] > 0 else 0
        ip = abs(idiff / pv["income"] * 100) if pv["income"] > 0 else 0
        await update.effective_message.reply_text(
            f"📊 <b>Analisis de Tendencia</b>\n\nGastos: {'📈' if ediff > 0 else '📉'} {h(f'{ep:.1f}')}%\nIngresos: {'📈' if idiff > 0 else '📉'} {h(f'{ip:.1f}')}%",
            parse_mode=ParseMode.HTML,
        )


async def cmd_panel(update, ctx):
    db = await get_db()
    uid = await get_or_create_user(db, update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    snapshot = await _build_financial_snapshot(db, uid)
    anomalies = await _build_anomalies(db, uid)
    await update.effective_message.reply_text(_format_panel_text(snapshot, anomalies), parse_mode=ParseMode.HTML)


async def cmd_anomalias(update, ctx):
    db = await get_db()
    uid = await get_or_create_user(db, update.effective_user.id)
    anomalies = await _build_anomalies(db, uid)
    if not anomalies:
        return await update.effective_message.reply_text("✅ No se detectaron anomalías de gasto este mes.")
    msg = "⚠️ <b>Anomalías de gasto</b>\n\n"
    for cat, cur, avg_prev in anomalies:
        msg += f"• {h(cat)}: €{h(f'{cur:.2f}')} vs media de 3 meses €{h(f'{avg_prev:.2f}')}\n"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_forecast(update, ctx):
    db = await get_db()
    uid = await get_or_create_user(db, update.effective_user.id)
    snapshot = await _build_financial_snapshot(db, uid)
    cash = snapshot["cash"]
    projected = snapshot["projected_balance"]
    msg = (
        f"🔮 <b>Forecast de fin de mes</b>\n\n"
        f"Saldo actual total: €{h(f'{cash:.2f}')}\n"
        f"Proyección al cierre: €{h(f'{projected:.2f}')}\n"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_tags(update, ctx):
    db = await get_db()
    uid = await get_or_create_user(db, update.effective_user.id)
    snapshot = await _build_financial_snapshot(db, uid)
    if not snapshot["tags"]:
        return await update.effective_message.reply_text("No hay etiquetas (#tag) en tus notas todavía.")
    msg = "🏷️ <b>Etiquetas detectadas</b>\n\n"
    for tag, count in sorted(snapshot["tags"].items(), key=lambda e: e[1], reverse=True):
        msg += f"• #{h(tag)}: {h(count)}\n"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_sugerircategoria(update, ctx):
    text = update.effective_message.text.replace("/sugerircategoria", "", 1).strip()
    if not text:
        return await update.effective_message.reply_text("Uso: /sugerircategoria <texto>\n\nEjemplo: /sugerircategoria supermercado mercadona")
    category = _smart_category_suggestion(text)
    if not category:
        return await update.effective_message.reply_text("No pude inferir una categoría clara. Prueba con más contexto.")
    await update.effective_message.reply_text(f"💡 Sugerencia: <b>{h(category)}</b>", parse_mode=ParseMode.HTML)


async def cmd_exportar(update, ctx):
    db = await get_db()
    uid = await get_or_create_user(db, update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
    c = await db.execute(
        "SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? ORDER BY t.date DESC",
        (uid,),
    )
    txs = await c.fetchall()
    if not txs:
        return await update.effective_message.reply_text("No hay transacciones para exportar.")
    await update.effective_message.reply_text("📥 Generando archivo CSV...", parse_mode=ParseMode.HTML)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Fecha", "Tipo", "Categoria", "Monto", "Cuenta", "Descripcion"])
    for tx in txs:
        w.writerow([tx["date"][:10], tx["type"], tx["category"], f"{tx['amount']:.2f}", tx["aname"], tx["description"] or ""])
    out.seek(0)
    bio = io.BytesIO(out.getvalue().encode("utf-8"))
    bio.name = f"finanzas_{datetime.now().strftime('%d-%m-%Y')}.csv"
    await update.effective_message.reply_document(bio)
