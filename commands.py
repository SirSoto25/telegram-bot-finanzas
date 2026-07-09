"""Command handlers for the finance bot."""
import json, math
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction

from _env import get_db
from finance_shared import (
    ACCOUNT_TYPE_MAP, CATEGORY_KBD_ITEMS, CATEGORY_MAP, FREQ_KBD_ITEMS,
    FREQ_MAP, MONTHS_ES, SESSION_TIMEOUT_MINUTES, TYPE_KBD_ITEMS,
    _cb_suffix_int, _cb_suffix_text, _extract_tags, end_of_month,
    _month_shift, _month_window, _smart_category_suggestion,
    h, parse_amount,
)
from finance_db import DBIntegrityError, _tx_wrap
import finance_reports
from finance_state import (
    _check_session_expiry, clear_session, get_accounts, get_or_create_user,
    get_roundup, get_session, save_session, get_system_state, save_system_state,
)
from finance_ui import _acct_kb, _confirm_kb, _kb, multi_kb
from finance_notifications import _check_budget_warning, _expense_ask_account, check_alerts
from finance_analytics import (
    _build_anomalies, _build_financial_snapshot, _format_panel_text,
    bar_chart, check_and_award_achievements, get_50_30_20, get_advice, get_burn_rate, get_goal_projections,
    get_monthly_tx, get_net_worth_history, get_phantom_expenses, get_streak, update_streak,
    get_savings_rate, get_yoy_comparison, predict_expenses, savings_recs, trend_chart, unicode_table,
)


async def cmd_start(update:Update,ctx):
    db=await get_db(); tid=update.effective_user.id
    await get_or_create_user(db,tid); await clear_session(db,tid)
    await update.effective_message.reply_text("""
📊 <b>Bot de Finanzas Personales</b>

¡Hola! Soy tu asistente de finanzas. Puedo ayudarte a:
✅ Gestionar tus cuentas (nomina, ahorros, cripto, inversiones)
✅ Registrar gastos e ingresos
✅ Configurar gastos recurrentes
✅ Ver resumenes y estadisticas

<b>Comandos disponibles:</b>
/cuentas - Ver tus cuentas
/nuevacuenta - Crear nueva cuenta
/gasto - Registrar un gasto
/ingreso - Registrar un ingreso
/recurrente - Gestionar gastos recurrentes
/resumen - Resumen del mes
/stats - Estadisticas por categoria
/help - Ver ayuda completa
""", parse_mode=ParseMode.HTML)


async def cmd_help(update,ctx):
    text = update.effective_message.text.strip()
    cmd = text.replace("/help", "", 1).strip().lstrip("/")
    if cmd:
        help_map = {
            "cuentas": "📋 <b>/cuentas</b>\nMuestra todas tus cuentas con sus saldos y el saldo total consolidado.",
            "nuevacuenta": "➕ <b>/nuevacuenta</b>\nCrea una nueva cuenta. Te pedirá nombre, tipo y saldo inicial.\n\nTipos: NOMINA, AHORROS, INVERSION, CRIPTO",
            "borrarcuenta": "🗑 <b>/borrarcuenta</b>\nElimina una cuenta y TODOS sus movimientos, recurrentes y alertas. Requiere confirmación.",
            "gasto": "💸 <b>/gasto</b>\nRegistra un gasto. Flujo: cantidad → categoría → fecha (opcional) → cuenta → nota (opcional).",
            "ingreso": "💰 <b>/ingreso</b>\nRegistra un ingreso. Flujo: cantidad → concepto → cuenta → nota (opcional).",
            "traspaso": "💱 <b>/traspaso</b>\nTransfiere dinero entre dos cuentas propias. Necesitas al menos 2 cuentas.",
            "deshacer": "↩️ <b>/deshacer</b>\nRevierte uno de los últimos 10 movimientos registrados.",
            "buscar": "🔍 <b>/buscar &lt;texto&gt;</b>\nBusca transacciones por palabra clave en descripciones y notas.\n\nEjemplo: /buscar supermercado",
            "resumen": "📊 <b>/resumen</b>\nResumen del mes actual: ingresos, gastos, balance, desglose por categoría con gráficos y recomendaciones.",
            "stats": "📈 <b>/stats</b>\nTabla de estadísticas de los últimos 6 meses con % de ahorro mensual.",
            "tendencia": "📉 <b>/tendencia</b>\nGráficos ASCII de tendencias de gastos e ingresos (12 meses).",
            "panel": "🖥 <b>/panel</b>\nPanel financiero completo con snapshot actual, próximos recurrentes y anomalías.",
            "patrimonio": "💰 <b>/patrimonio</b>\nEvolución del patrimonio neto en los últimos 12 meses con tabla mensual y variación total.",
            "comparar": "📊 <b>/comparar</b>\nComparativa de gastos por categoría del mes actual vs el mismo mes del año anterior.",
            "burnrate": "🔥 <b>/burnrate</b>\nCalcula cuántos días durará tu saldo al ritmo actual de gasto diario.",
            "ahorro": "🐷 <b>/ahorro</b>\nTasa de ahorro mensual (% de ingresos ahorrados) de los últimos 6 meses.",
            "consejo": "🧠 <b>/consejo</b>\nConsejos personalizados basados en tus patrones de gasto, burn rate y comparativas.",
            "regla": "📐 <b>/regla</b>\nVisualización de la regla 50/30/20 (necesidades/deseos/ahorro) de este mes.",
            "proyeccion": "🎯 <b>/proyeccion</b>\nCuánto falta para alcanzar cada meta según tu ritmo de ahorro actual.",
            "fantasmas": "👻 <b>/fantasmas</b>\nDetecta gastos duplicados o frecuentes que podrían ser fugas de dinero.",
            "forecast": "🔮 <b>/forecast</b>\nProyección del saldo al cierre del mes basada en el gasto diario promedio.",
            "anomalias": "⚠️ <b>/anomalias</b>\nDetecta categorías con gasto anormalmente alto este mes vs media de los últimos 3 meses.",
            "tags": "🏷️ <b>/tags</b>\nLista todas las etiquetas (#tag) usadas en notas, ordenadas por frecuencia.",
            "exportar": "📥 <b>/exportar</b>\nDescarga un archivo CSV con todas tus transacciones.",
            "presupuesto": "📋 <b>/presupuesto</b>\nMuestra presupuestos del mes con barras de progreso visuales.",
            "presupuestoset": "⚙️ <b>/presupuestoset</b>\nCrea o modifica un presupuesto mensual para una categoría.",
            "metas": "🎯 <b>/metas</b>\nLista tus metas de ahorro con barras de progreso.",
            "nuevameta": "🆕 <b>/nuevameta</b>\nCrea una nueva meta de ahorro con nombre, objetivo y fecha límite opcional.",
            "aportarmeta": "💪 <b>/aportarmeta</b>\nAporta dinero desde una cuenta a una meta de ahorro existente.",
            "alertas": "🔔 <b>/alertas</b>\nGestiona alertas de saldo bajo en tus cuentas.",
            "agregaralerta": "➕🔔 <b>/agregaralerta</b>\nCrea una alerta para cuando el saldo de una cuenta baje de un umbral.",
            "recurrente": "🔁 <b>/recurrente</b>\nLista tus gastos recurrentes configurados.",
            "agregarrecurrente": "➕🔁 <b>/agregarrecurrente</b>\nAñade un gasto recurrente (Netflix, alquiler, etc.) con frecuencia y categoría.",
            "ingresorecurrente": "📥🔁 <b>/ingresorecurrente</b>\nLista tus ingresos recurrentes (nómina, rentas, etc.).",
            "agregaringresorecurrente": "➕📥 <b>/agregaringresorecurrente</b>\nAñade un ingreso recurrente (nómina, renta, dividendo, etc.).",
            "redondeo": "🪙 <b>/redondeo</b>\nMuestra el estado del redondeo automático: cada gasto se redondea al € superior y la diferencia se ahorra.",
            "redondeotoggle": "🪙🔘 <b>/redondeotoggle</b>\nActiva o desactiva el redondeo automático.",
            "redondeocuenta": "🪙💼 <b>/redondeocuenta</b>\nCambia la cuenta destino donde se acumula el dinero del redondeo.",
            "sugerircategoria": "💡 <b>/sugerircategoria &lt;texto&gt;</b>\nSugiere una categoría basada en el texto.\n\nEjemplo: /sugerircategoria supermercado",
            "reset": "⚠️ <b>/reset</b>\nBorra TODOS tus datos permanentemente. Requiere confirmación explícita.",
            "menu": "🏠 <b>/menu</b>\nAbre el panel principal con 12 botones interactivos para todas las funciones.",
            "cancel": "❌ <b>/cancel</b>\nCancela la operación en curso y limpia la sesión.",
            "g": "💨 <b>/g &lt;cantidad&gt; [descripción]</b>\nRegistra un gasto rápido en un solo mensaje. Infiere la categoría automáticamente.\n\nEjemplo: /g 25.50 mercadona",
            "resumendiario": "☀️ <b>/resumendiario [on|off]</b>\nActiva o desactiva el resumen diario automático. Recibirás un balance cada mañana a las 8am.",
            "factura": "🧾 <b>/factura</b>\nCrea un recordatorio de factura mensual (luz, internet, alquiler). Te avisará el día configurado.",
            "facturas": "📋 <b>/facturas</b>\nLista tus recordatorios de facturas configurados.",
            "borrarfactura": "🗑 <b>/borrarfactura</b>\nElimina un recordatorio de factura.",
            "racha": "🔥 <b>/racha</b>\nMuestra tu racha de meses consecutivos ahorrando y tu mejor récord.",
            "logros": "🏆 <b>/logros</b>\nMuestra los logros desbloqueados (7 disponibles: primer gasto, 100 transacciones, racha de 3 meses...).",
            "resumenanual": "🎉 <b>/resumen2025</b>\nResumen anual con ingresos, gastos, % ahorrado y top 3 categorías de gasto.",
            "split": "💸 <b>/split &lt;cantidad&gt; @usuario1 @usuario2 ...</b>\nDivide un gasto entre varias personas. Cada una deberá su parte.\n\nEjemplo: /split 60 @juan @maria cena",
            "deudas": "💳 <b>/deudas</b>\nMuestra quién te debe dinero y a quién le debes (gastos compartidos).",
        }
        msg = help_map.get(cmd, f"❓ Comando <b>/{cmd}</b> no encontrado.\nUsa /help para ver todos los comandos.")
        return await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

    await update.effective_message.reply_text("""
📚 <b>Guía de comandos</b> — usa <b>/help [comando]</b> para detalles

<b>💼 Cuentas</b>
/cuentas /nuevacuenta /borrarcuenta

<b>💸 Transacciones</b>
/g /gasto /ingreso /traspaso /deshacer /buscar

<b>📊 Reportes</b>
/resumen /stats /tendencia /panel /forecast /anomalias /tags /exportar
/patrimonio /comparar /burnrate /ahorro

<b>🧠 Coaching</b>
/consejo /regla /proyeccion /fantasmas

<b>🎯 Presupuestos y metas</b>
/presupuesto /presupuestoset /metas /nuevameta /aportarmeta

<b>🔔 Alertas, facturas y recurrentes</b>
/alertas /agregaralerta /borraralerta /recurrente /agregarrecurrente /borrarrecurrente
/ingresorecurrente /agregaringresorecurrente /factura /facturas /borrarfactura

<b>🏆 Gamificación</b>
/racha /logros /resumenanual

<b>💳 Gastos compartidos</b>
/split /deudas

<b>🪙 Redondeo</b>
/redondeo /redondeotoggle /redondeocuenta

<b>⚙️ Otros</b>
/start /menu /cancel /sugerircategoria /reset /resumendiario
""", parse_mode=ParseMode.HTML)


async def cmd_menu(update,ctx):
    db=await get_db(); await clear_session(db,update.effective_user.id)
    MENU_ITEMS = [
        ("💸 Registrar Gasto","menu_gasto"),("💰 Registrar Ingreso","menu_ingreso"),
        ("💱 Transferencia","menu_traspaso"),("↩️ Deshacer","menu_deshacer"),
        ("📊 Resumen Mensual","menu_resumen"),("📈 Estadisticas","menu_stats"),
        ("📅 Recurrentes","menu_recurrente"),("🔔 Alertas","menu_alertas"),
        ("💼 Ver Cuentas","menu_cuentas"),("🪙 Redondeo","menu_redondeo"),
        ("📥 Exportar CSV","menu_exportar"),("❓ Ayuda","menu_help"),
    ]
    await update.effective_message.reply_text("📊 <b>Panel Principal</b>\n\n¿Que quieres hacer?",
                                     reply_markup=multi_kb(MENU_ITEMS,"menu",cols=2), parse_mode=ParseMode.HTML)


async def cmd_cancel(update,ctx):
    db=await get_db(); await clear_session(db,update.effective_user.id)
    await update.effective_message.reply_text("✅ Operacion cancelada.", parse_mode=ParseMode.HTML)


async def cmd_cuentas(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    accts=await get_accounts(db,uid)
    if not accts: return await update.effective_message.reply_text("No tienes cuentas. Usa /nuevacuenta para crear una.", parse_mode=ParseMode.HTML)
    rows=[]; total=0.0
    for a in accts:
        rows.append([a['name'],a['type'],f"€{a['balance']:.2f}"]); total+=a["balance"]
    tbl=unicode_table(["Cuenta","Tipo","Saldo"],rows)
    await update.effective_message.reply_text(f"💰 <b>Tus cuentas:</b>\n<pre>{h(tbl)}</pre>\n<b>Saldo total: €{h(f'{total:.2f}')}</b>", parse_mode=ParseMode.HTML)


async def cmd_nueva_cuenta(update,ctx):
    db=await get_db(); await save_session(db,update.effective_user.id,"waiting_account_name")
    await update.effective_message.reply_text("¿Cual es el nombre de la cuenta?\n(Ejemplos: Nomina, Ahorros, Cripto)\n\n/cancel para cancelar")


async def cmd_borrar_cuenta(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    accts=await get_accounts(db,uid)
    if not accts: return await update.effective_message.reply_text("No tienes cuentas para eliminar.")
    await update.effective_message.reply_text("🗑 <b>Eliminar cuenta</b>\n\nSelecciona la cuenta a eliminar.\n⚠️ Se eliminaran tambien sus transacciones y recurrentes asociados.",
                                     reply_markup=_acct_kb(accts,"del_account"), parse_mode=ParseMode.HTML)


async def cmd_gasto(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    if not await get_accounts(db,uid): return await update.effective_message.reply_text("Debes crear una cuenta primero con /nuevacuenta")
    await save_session(db,update.effective_user.id,"waiting_expense_amount")
    await update.effective_message.reply_text("¿Cuanto gastaste?\n(Formato: cantidad)\n\nEjemplos: 45.50, 100\n\n/cancel para cancelar")


async def cmd_ingreso(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    if not await get_accounts(db,uid): return await update.effective_message.reply_text("Debes crear una cuenta primero con /nuevacuenta")
    await save_session(db,update.effective_user.id,"waiting_income_amount")
    await update.effective_message.reply_text("¿Cuanto ingreso?\n(Formato: cantidad)\n\nEjemplos: 100, 2500.50\n\n/cancel para cancelar")


async def cmd_traspaso(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if len(accts)<2: return await update.effective_message.reply_text("Necesitas al menos 2 cuentas para transferir. Crea otra con /nuevacuenta", parse_mode=ParseMode.HTML)
    await save_session(db,tid,"waiting_transfer_from")
    await update.effective_message.reply_text("💱 <b>Transferencia</b>\n\nSelecciona la cuenta de ORIGEN:", reply_markup=_acct_kb(accts,"xfer_from",None), parse_mode=ParseMode.HTML)


async def cmd_gasto_rapido(update,ctx):
    from finance_shared import parse_quick_expense
    text=update.effective_message.text.strip()
    amt,desc,cat=parse_quick_expense(text)
    if amt is None or amt<=0:
        return await update.effective_message.reply_text("Uso: /g &lt;cantidad&gt; [descripción]\n\nEjemplos:\n/g 25.50 mercadona\n/g 10 cafe", parse_mode=ParseMode.HTML)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if not accts:
        return await update.effective_message.reply_text("Debes crear una cuenta primero con /nuevacuenta")
    cat = cat or "Otros"
    sdata={"quickAmount":amt,"quickDesc":desc or "","quickCat":cat}
    if len(accts)==1:
        await _finalize_quick_expense(db,tid,uid,sdata,accts[0]["id"],update)
    else:
        await save_session(db,tid,"quick_expense_account",sdata)
        await update.effective_message.reply_text(
            f"💸 Gasto rápido: <b>€{h(f'{amt:.2f}')}</b> en <b>{h(desc or '(sin descripción)')}</b>\nCategoria sugerida: <b>{h(cat)}</b>\n\nSelecciona la cuenta:",
            reply_markup=_acct_kb(accts,"quick_acc"), parse_mode=ParseMode.HTML)


async def _finalize_quick_expense(db,tid,uid,sdata,aid,update):
    """Finalize a quick expense without leaving a flow open."""
    amt=sdata["quickAmount"]; desc=sdata.get("quickDesc",""); cat=sdata["quickCat"]
    now=datetime.now().isoformat()
    await db.execute("INSERT INTO transactions(user_id,account_id,amount,type,category,date,description) VALUES(?,?,?,'GASTO',?,?,?)",(uid,aid,amt,cat,now,desc))
    await db.execute("UPDATE accounts SET balance=balance-? WHERE id=?",(amt,aid))
    await db.commit(); await clear_session(db,tid)
    label=f"\n📝 {h(desc)}" if desc else ""
    await update.effective_message.reply_text(
        f"✅ Gasto registrado\n💸 €{h(f'{amt:.2f}')}\n📌 {h(cat)}{label}",
        parse_mode=ParseMode.HTML)


async def cmd_deshacer(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM transactions WHERE user_id=? AND type IN ('GASTO','INGRESO','TRANSFERENCIA') ORDER BY id DESC LIMIT 10",(uid,))
    txs=await c.fetchall()
    if not txs: return await update.effective_message.reply_text("No hay movimientos recientes para deshacer.")
    btns=[]
    for tx in txs:
        typetag="💸" if tx["type"]=="GASTO" else ("💰" if tx["type"]=="INGRESO" else "💱")
        datepart=tx["date"][:10] if tx["date"] else "—"
        label=f"{typetag} {datepart} | {tx['category']} | €{tx['amount']:.2f}"
        btns.append((label,f"undo_{tx['id']}"))
    btns.append(("Cancelar","cancel_action"))
    await update.effective_message.reply_text("↩️ <b>Deshacer movimiento</b>\n\nSelecciona el movimiento a deshacer (ultimos 10):", reply_markup=_kb(btns), parse_mode=ParseMode.HTML)


async def cmd_redondeo(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    rup=await get_roundup(db,uid)
    msg="🪙 <b>Redondeo Automatico</b>\n\n"
    if rup and rup["enabled"]:
        c=await db.execute("SELECT name FROM accounts WHERE id=?",(rup["account_id"],)); acc=await c.fetchone()
        accname=h(acc["name"]) if acc else "—"
        msg+=f"Estado: ✅ <b>ACTIVADO</b>\n"
        msg+=f"Cuenta destino: <b>{accname}</b>\n\n"
        msg+="Cada gasto se redondea al euro superior y la diferencia se transfiere a la cuenta destino.\n\n"
    else:
        msg+="Estado: ❌ <b>DESACTIVADO</b>\n\n"
        msg+="El redondeo redondea cada gasto al euro superior y ahorra la diferencia automaticamente.\n\n"
    msg+="/redondeotoggle - Activar o desactivar\n/redondeocuenta - Cambiar cuenta destino"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_redondeo_toggle(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    rup=await get_roundup(db,uid)
    if rup and rup["enabled"]:
        await db.execute("UPDATE roundup_config SET enabled=0 WHERE user_id=?",(uid,)); await db.commit()
        await update.effective_message.reply_text("🪙 Redondeo <b>DESACTIVADO</b>", parse_mode=ParseMode.HTML)
    else:
        accts=await get_accounts(db,uid)
        if not accts: return await update.effective_message.reply_text("Necesitas al menos una cuenta. Crea una con /nuevacuenta", parse_mode=ParseMode.HTML)
        if not rup: await db.execute("INSERT INTO roundup_config(user_id,enabled,account_id) VALUES(?,1,?)",(uid,accts[0]["id"]))
        else: await db.execute("UPDATE roundup_config SET enabled=1 WHERE user_id=?",(uid,))
        await db.commit()
        await update.effective_message.reply_text("🪙 Redondeo <b>ACTIVADO</b>\n\nCada gasto se redondeara al euro superior y la diferencia se ahorrara automaticamente.", parse_mode=ParseMode.HTML)


async def cmd_redondeo_cuenta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if not accts: return await update.effective_message.reply_text("Necesitas al menos una cuenta.")
    await save_session(db,tid,"waiting_roundup_account")
    await update.effective_message.reply_text("🪙 <b>Cuenta destino del redondeo</b>\n\nSelecciona a que cuenta ira el dinero redondeado:", reply_markup=_acct_kb(accts,"roundup_acc",None), parse_mode=ParseMode.HTML)


async def cmd_recurrente(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM recurring_expenses WHERE user_id=? ORDER BY next_date",(uid,))
    recs=await c.fetchall()
    if not recs: return await update.effective_message.reply_text("No tienes gastos recurrentes configurados.\n\n/agregarrecurrente - Agregar nuevo gasto", parse_mode=ParseMode.HTML)
    headers=["Nombre","Monto","Frecuencia","Proximo","ID"]
    rows=[]; total=0.0
    for r in recs:
        nd=r["next_date"][:10] if r["next_date"] else "—"
        rows.append([r['name'],f"€{r['amount']:.2f}",r['frequency'],nd,str(r['id'])])
        if r["frequency"]=="MENSUAL": total+=r["amount"]
    tbl=unicode_table(headers,rows)
    msg=f"📅 <b>Gastos recurrentes:</b>\n<pre>{h(tbl)}</pre>\n<b>Total estimado mensual: €{h(f'{total:.2f}')}</b>\n\n"
    msg+="¿Que deseas hacer?\n/agregarrecurrente - Agregar nuevo gasto\n/borrarrecurrente - Eliminar gasto\n/cancel - Cancelar"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)
    await save_session(db,update.effective_user.id,"menu_recurrente")


async def cmd_agregar_recurrente(update,ctx):
    db=await get_db(); await save_session(db,update.effective_user.id,"waiting_recurring_name")
    await update.effective_message.reply_text("¿Cual es el nombre del gasto recurrente?\n(Ejemplo: Netflix, Seguro del coche)\n\n/cancel para cancelar")


async def cmd_borrar_recurrente(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM recurring_expenses WHERE user_id=?",(uid,)); recs=await c.fetchall()
    if not recs: return await update.effective_message.reply_text("No tienes gastos recurrentes para eliminar.")
    btns=[(f"{r['name']} — €{r['amount']:.2f} ({r['frequency']})",f"del_recurring_{r['id']}") for r in recs]
    btns.append(("Cancelar","cancel_action"))
    await update.effective_message.reply_text("Selecciona el gasto recurrente a eliminar:", reply_markup=_kb(btns))


async def cmd_stats(update,ctx):
    return await finance_reports.cmd_stats(update,ctx)


async def cmd_presupuesto(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    now=datetime.now(); month=f"{now.year}-{now.month:02d}"
    c=await db.execute("SELECT category,amount FROM budgets WHERE user_id=? AND month=?",(uid,month))
    budgets=await c.fetchall()
    start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    end=end_of_month(now)
    c2=await db.execute("SELECT category,SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=? GROUP BY category",(uid,start.isoformat(),end.isoformat()))
    spent={r["category"]:r["total"] for r in await c2.fetchall()}
    if not budgets: return await update.effective_message.reply_text("No tienes presupuestos configurados.\n\nUsa /presupuestoset para crear uno.",parse_mode=ParseMode.HTML)
    msg=f"📊 <b>Presupuestos de {h(MONTHS_ES[now.month])} {h(str(now.year))}</b>\n\n"
    for b in budgets:
        s=spent.get(b["category"],0); pct=s/b["amount"]*100; bar_w=10
        bl=min(int(pct/100*bar_w),bar_w); bar="█"*bl+"░"*(bar_w-bl)
        icon="🔴" if pct>90 else ("🟡" if pct>70 else "🟢")
        bud_amt = "{:.2f}".format(b["amount"]); spent_amt = "{:.2f}".format(s); pct_str = "{:.1f}".format(pct)
        msg+=f"{icon} {h(b['category'])}: €{h(spent_amt)}/{h(bud_amt)} {bar} {h(pct_str)}%\n"
    msg+="\n/presupuestoset - Crear o modificar presupuesto"
    await update.effective_message.reply_text(msg,parse_mode=ParseMode.HTML)


async def cmd_presupuestoset(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    await save_session(db,tid,"waiting_budget_category")
    await update.effective_message.reply_text("Selecciona la categoria para el presupuesto:",reply_markup=multi_kb(CATEGORY_KBD_ITEMS,"budcat",cols=2,extra=None))


async def cmd_buscar(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    text=update.effective_message.text.strip()
    parts=text.split(" ",1)
    keyword=parts[1] if len(parts)>1 else ""
    if not keyword: return await update.effective_message.reply_text("Uso: /buscar &lt;palabra clave&gt;\n\nBusca en las descripciones de tus transacciones.",parse_mode=ParseMode.HTML)
    c=await db.execute("SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? AND t.description LIKE ? ORDER BY t.date DESC LIMIT 10",(uid,f"%{keyword}%"))
    txs=await c.fetchall()
    if not txs: return await update.effective_message.reply_text(f"No se encontraron transacciones para: {h(keyword)}",parse_mode=ParseMode.HTML)
    msg=f"🔍 <b>Resultados para: {h(keyword)}</b>\n\n"
    for tx in txs:
        dt=tx["date"][:10] if tx["date"] else "—"; desc=tx["description"] or "—"
        tx_amt = "{:.2f}".format(tx["amount"])
        msg+=f"{'💸' if tx['type']=='GASTO' else ('💰' if tx['type']=='INGRESO' else '💱')} {dt} | {tx['category']} | €{h(tx_amt)} | {tx['aname']}\n  {desc}\n\n"
    await update.effective_message.reply_text(msg,parse_mode=ParseMode.HTML)


async def cmd_metas(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM savings_goals WHERE user_id=? ORDER BY created_at",(uid,))
    goals=await c.fetchall()
    if not goals: return await update.effective_message.reply_text("No tienes metas de ahorro.\n\n/nuevameta - Crear nueva meta",parse_mode=ParseMode.HTML)
    msg="🎯 <b>Metas de Ahorro</b>\n\n"
    for g in goals:
        g_curr = "{:.2f}".format(g["current_amount"]); g_targ = "{:.2f}".format(g["target_amount"])
        pct=g["current_amount"]/g["target_amount"]*100; bar_w=10
        bl=min(int(pct/100*bar_w),bar_w); bar="█"*bl+"░"*(bar_w-bl)
        deadline=f" - Vence: {g['deadline'][:10]}" if g["deadline"] else ""
        msg+=f"🎯 {h(g['name'])}: €{h(g_curr)}/{h(g_targ)} {bar} {h(f'{pct:.1f}')}%{deadline}\n\n"
    msg+="/nuevameta - Crear meta\n/aportarmeta - Aportar a meta"
    await update.effective_message.reply_text(msg,parse_mode=ParseMode.HTML)


async def cmd_nuevameta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    await save_session(db,tid,"waiting_goal_name")
    await update.effective_message.reply_text("¿Nombre de la meta?\n(Ejemplo: Viaje a Japon, Fondo de emergencia)\n\n/cancel para cancelar")


async def cmd_aportarmeta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    c=await db.execute("SELECT * FROM savings_goals WHERE user_id=?",(uid,)); goals=await c.fetchall()
    if not goals: return await update.effective_message.reply_text("No tienes metas. Crea una con /nuevameta")
    btns=[(f"{g['name']} (€{g['current_amount']:.2f}/{g['target_amount']:.2f})",f"aportar_goal_{g['id']}") for g in goals]
    btns.append(("Cancelar","cancel_action"))
    await update.effective_message.reply_text("Selecciona la meta a la que quieres aportar:",reply_markup=_kb(btns))


async def cmd_ingresorecurrente(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM recurring_expenses WHERE user_id=? AND type='INGRESO' ORDER BY next_date",(uid,))
    recs=await c.fetchall()
    if not recs: return await update.effective_message.reply_text("No tienes ingresos recurrentes configurados.\n\n/agregaringresorecurrente - Agregar ingreso recurrente",parse_mode=ParseMode.HTML)
    headers=["Nombre","Monto","Frecuencia","Proximo","ID"]
    rows=[]; total=0.0
    for r in recs:
        nd=r["next_date"][:10] if r["next_date"] else "—"
        rows.append([r['name'],f"€{r['amount']:.2f}",r['frequency'],nd,str(r['id'])])
        if r["frequency"]=="MENSUAL": total+=r["amount"]
    tbl=unicode_table(headers,rows)
    msg=f"💰 <b>Ingresos recurrentes:</b>\n<pre>{h(tbl)}</pre>\n<b>Total estimado mensual: €{h(f'{total:.2f}')}</b>\n\n"
    msg+="/agregaringresorecurrente - Agregar ingreso recurrente\n/cancel - Cancelar"
    await update.effective_message.reply_text(msg,parse_mode=ParseMode.HTML)


async def cmd_agregaringresorecurrente(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts = await get_accounts(db, uid)
    if not accts:
        return await update.effective_message.reply_text("Crea una cuenta primero con /nuevacuenta")
    await save_session(db,tid,"waiting_recurring_income_name")
    await update.effective_message.reply_text("¿Nombre del ingreso recurrente?\n(Ejemplo: Nomina, Renta, Dividendo)\n\n/cancel para cancelar")


async def cmd_tendencia(update,ctx):
    return await finance_reports.cmd_tendencia(update,ctx)


async def cmd_panel(update,ctx):
    return await finance_reports.cmd_panel(update,ctx)


async def cmd_anomalias(update,ctx):
    return await finance_reports.cmd_anomalias(update,ctx)


async def cmd_forecast(update,ctx):
    return await finance_reports.cmd_forecast(update,ctx)


async def cmd_tags(update,ctx):
    return await finance_reports.cmd_tags(update,ctx)


async def cmd_patrimonio(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    history=await get_net_worth_history(db,uid,months=12)
    if not history:
        return await update.effective_message.reply_text("No tienes transacciones para calcular el patrimonio.", parse_mode=ParseMode.HTML)
    rows=[(m,f"€{n:.2f}",f"+€{i:.2f}",f"-€{e:.2f}") for m,n,i,e in history]
    tbl=unicode_table(["Mes","Patrimonio","Ingresos","Gastos"],rows)
    current=history[-1][1]
    oldest=history[0][1]
    diff=current-oldest; pct=(diff/abs(oldest)*100) if oldest!=0 else 0
    trend_icon="📈" if diff>0 else ("📉" if diff<0 else "➡️")
    await update.effective_message.reply_text(
        f"💰 <b>Evolución del patrimonio</b> (12 meses)\n<pre>{h(tbl)}</pre>\n{trend_icon} Variación: <b>€{h(f'{diff:+.2f}')}</b> ({h(f'{pct:+.1f}')}%)",
        parse_mode=ParseMode.HTML)


async def cmd_comparar(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    comp=await get_yoy_comparison(db,uid)
    if not comp["rows"]:
        return await update.effective_message.reply_text("No hay datos suficientes para comparar.", parse_mode=ParseMode.HTML)
    tbl=unicode_table(["Categoria",comp["current_month"],comp["prev_month"],"Var"],comp["rows"])
    total_arrow="📈" if comp["total_pct"]>0 else ("📉" if comp["total_pct"]<0 else "➡️")
    await update.effective_message.reply_text(
        f"📊 <b>Comparativa {comp['current_month']} vs {comp['prev_month']}</b>\n<pre>{h(tbl[:3500])}</pre>\n"
        f"{total_arrow} Total: <b>€{h(f'{comp['cur_total']:.2f}')}</b> vs €{h(f'{comp['prev_total']:.2f}')} ({h(f'{comp['total_pct']:+.0f}')}%)",
        parse_mode=ParseMode.HTML)


async def cmd_burnrate(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    br=await get_burn_rate(db,uid)
    days_msg=f"≈ <b>{br['days_left']} días</b>" if br["days_left"] is not None else "∞ (no hay gastos)"
    await update.effective_message.reply_text(
        f"🔥 <b>Burn Rate</b>\n\n"
        f"💰 Saldo total: <b>€{h(f'{br['total_balance']:.2f}')}</b>\n"
        f"📉 Gasto medio diario (este mes): <b>€{h(f'{br['daily_avg']:.2f}')}</b>\n"
        f"⏱ Días hasta agotar saldo: {days_msg}\n"
        f"📅 Proyección fin de mes: <b>€{h(f'{br['month_projection']:.2f}')}</b> en {br['days_in_month']-br['days_elapsed']} días restantes",
        parse_mode=ParseMode.HTML)


async def cmd_ahorro(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    rates,avg=await get_savings_rate(db,uid)
    if not rates:
        return await update.effective_message.reply_text("No hay datos suficientes para calcular la tasa de ahorro.", parse_mode=ParseMode.HTML)
    rows=[(m,f"{r:+.0f}%",f"€{i:.0f}",f"€{e:.0f}") for m,r,i,e in rates]
    tbl=unicode_table(["Mes","% Ahorro","Ingresos","Gastos"],rows)
    icon="🟢" if avg>20 else ("🟡" if avg>0 else "🔴")
    await update.effective_message.reply_text(
        f"🐷 <b>Tasa de ahorro</b> (6 meses)\n<pre>{h(tbl[:3500])}</pre>\n{icon} Media: <b>{h(f'{avg:+.0f}')}%</b>",
        parse_mode=ParseMode.HTML)


async def cmd_consejo(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    history=await get_net_worth_history(db,uid,months=3)
    burn=await get_burn_rate(db,uid)
    comp=await get_yoy_comparison(db,uid)
    rates,avg_rate=await get_savings_rate(db,uid,months=1)
    tips=get_advice(history,burn,comp,round(avg_rate))
    await update.effective_message.reply_text(
        f"🧠 <b>Consejo financiero personalizado</b>\n\n" + "\n".join(tips),
        parse_mode=ParseMode.HTML)


async def cmd_regla(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    d=await get_50_30_20(db,uid)
    if d["income"]<=0:
        return await update.effective_message.reply_text("No hay ingresos este mes para calcular la regla 50/30/20.", parse_mode=ParseMode.HTML)
    def bar(label,actual,ideal):
        pct=min(int(actual/d["income"]*100),100) if d["income"]>0 else 0
        ipct=min(int(ideal/d["income"]*100),100) if d["income"]>0 else 0
        bar_filled="█"*(pct//5); bar_ideal="▌"*(ipct//5)
        return f"{label}: <b>€{actual:.0f}</b> ({pct}%) [ideal: €{ideal:.0f} ({ipct}%)]"
    await update.effective_message.reply_text(
        f"📐 <b>Regla 50/30/20</b> — este mes\n\n"
        f"{bar('🏠 Necesidades',d['necesidades'],d['ideal_n'])}\n"
        f"{bar('🎮 Deseos',d['deseos'],d['ideal_d'])}\n"
        f"{bar('🐷 Ahorro',d['ahorro'],d['ideal_a'])}\n\n"
        f"💰 Ingresos: <b>€{d['income']:.0f}</b>",
        parse_mode=ParseMode.HTML)


async def cmd_proyeccion(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    projections=await get_goal_projections(db,uid)
    if not projections:
        return await update.effective_message.reply_text("No tienes metas de ahorro. Crea una con /nuevameta", parse_mode=ParseMode.HTML)
    rows=[]
    for p in projections:
        pct=(p["current"]/p["target"]*100) if p["target"]>0 else 0
        bar="▓"*int(pct//10)+"░"*(10-int(pct//10))
        deadline_info=""
        if p["deadline"]:
            deadline_info=f"\n   📅 Límite: {p['deadline']}"
        rows.append((f"{p['name']} {bar} {pct:.0f}%",f"€{p['current']:.0f}/{p['target']:.0f} → {p['eta']}{deadline_info}"))
    tbl=unicode_table(["Meta","Progreso → ETA"],rows)
    await update.effective_message.reply_text(
        f"🎯 <b>Proyección de metas</b>\n<pre>{h(tbl[:3500])}</pre>\nBasado en tu ahorro medio de los últimos 12 meses.",
        parse_mode=ParseMode.HTML)


async def cmd_fantasmas(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    phantoms=await get_phantom_expenses(db,uid)
    if not phantoms:
        return await update.effective_message.reply_text("👻 No se detectaron gastos duplicados en los últimos 60 días.", parse_mode=ParseMode.HTML)
    rows=[(d,c,f"{n}x",f"€{t:.2f}") for d,c,n,t in phantoms]
    tbl=unicode_table(["Descripción","Categoria","Veces","Total"],rows)
    total_sum=sum(t for _,_,_,t in phantoms)
    await update.effective_message.reply_text(
        f"👻 <b>Gastos fantasma</b> (detectados en 60 días)\n<pre>{h(tbl[:3500])}</pre>\n💸 Total detectado: <b>€{h(f'{total_sum:.2f}')}</b>",
        parse_mode=ParseMode.HTML)


async def cmd_resumendiario(update,ctx):
    text=update.effective_message.text.strip()
    arg=text.replace("/resumendiario","",1).strip().lower()
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    if arg in ("on","si","activar","1","true"):
        await db.execute("UPDATE users SET daily_summary_enabled=true WHERE telegram_id=?",(tid,))
        await db.commit()
        return await update.effective_message.reply_text("✅ Resumen diario <b>activado</b>. Recibirás un resumen cada mañana a las 8am.", parse_mode=ParseMode.HTML)
    elif arg in ("off","no","desactivar","0","false"):
        await db.execute("UPDATE users SET daily_summary_enabled=false WHERE telegram_id=?",(tid,))
        await db.commit()
        return await update.effective_message.reply_text("🔕 Resumen diario <b>desactivado</b>.", parse_mode=ParseMode.HTML)
    else:
        row=await (await db.execute("SELECT daily_summary_enabled FROM users WHERE telegram_id=?",(tid,))).fetchone()
        enabled=row["daily_summary_enabled"] if row else False
        status="✅ Activo" if enabled else "❌ Inactivo"
        return await update.effective_message.reply_text(
            f"📋 <b>Resumen diario</b>\n\nEstado: {status}\n\nUsa <b>/resumendiario on</b> o <b>/resumendiario off</b> para cambiar.",
        parse_mode=ParseMode.HTML)


async def cmd_racha(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    s=await get_streak(db,uid)
    if not s:
        return await update.effective_message.reply_text("🔥 Aún no tienes racha. Registra ingresos y gastos para empezar a construir tu historial.", parse_mode=ParseMode.HTML)
    await update.effective_message.reply_text(
        f"🔥 <b>Racha de ahorro</b>\n\nRacha actual: <b>{s['current_streak']} meses</b>\n🏆 Mejor racha: <b>{s['best_streak']} meses</b>\n\nCada mes con saldo positivo suma 1 a tu racha.",
        parse_mode=ParseMode.HTML)


async def cmd_logros(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    unlocked=await db._select_rows("achievements", filters=[("eq", "user_id", uid)])
    rows=[]
    for a in unlocked:
        rows.append(a["achievement_key"])
    all_defs={
        "first_expense":"🏁 Primer gasto registrado","first_account":"🏦 Primera cuenta creada",
        "first_goal":"🎯 Primera meta creada","transactions_100":"📊 100 transacciones",
        "full_month":"📅 1 mes completo trackeado","streak_3":"🔥 3 meses de racha de ahorro",
        "big_saver":"💎 Ahorro >20% del ingreso mensual",
    }
    lines=[f"{'✅' if d['key'] in rows else '🔒'} {d['msg']}" for key,d in [("first_expense",{"key":"first_expense","msg":all_defs["first_expense"]}),("first_account",{"key":"first_account","msg":all_defs["first_account"]}),("first_goal",{"key":"first_goal","msg":all_defs["first_goal"]}),("transactions_100",{"key":"transactions_100","msg":all_defs["transactions_100"]}),("full_month",{"key":"full_month","msg":all_defs["full_month"]}),("streak_3",{"key":"streak_3","msg":all_defs["streak_3"]})] + [("big_saver",{"key":"big_saver","msg":all_defs["big_saver"]})]]
    lines=[f"{'✅' if k in rows else '🔒'} {v}" for k,v in all_defs.items()]
    await update.effective_message.reply_text(
        f"🏆 <b>Tus logros</b> ({len(rows)}/7)\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML)


async def cmd_resumen_anual(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    text=update.effective_message.text.strip()
    year_str=text.replace("/resumen","",1).strip()
    try:
        year=int(year_str) if year_str else datetime.now().year
    except ValueError:
        year=datetime.now().year
    start=f"{year}-01-01"; end=f"{year}-12-31"
    c=await db.execute("SELECT type,amount,category FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA'",(uid,start,end))
    rows=await c.fetchall()
    if not rows:
        return await update.effective_message.reply_text(f"No hay transacciones para {year}.", parse_mode=ParseMode.HTML)
    income=sum(r["amount"] for r in rows if r["type"]=="INGRESO")
    expense=sum(r["amount"] for r in rows if r["type"]=="GASTO")
    by_cat={}
    for r in rows:
        if r["type"]=="GASTO":
            by_cat[r["category"]]=by_cat.get(r["category"],0)+r["amount"]
    top=sorted(by_cat.items(),key=lambda x:x[1],reverse=True)[:3]
    top_lines="\n".join(f"  {i+1}. {c}: €{a:.2f}" for i,(c,a) in enumerate(top))
    await update.effective_message.reply_text(
        f"🎉 <b>Resumen {year}</b>\n\n"
        f"💰 Ingresos: <b>€{income:.2f}</b>\n💸 Gastos: <b>€{expense:.2f}</b>\n"
        f"🐷 Ahorrado: <b>€{income-expense:+.2f}</b> ({(income-expense)/income*100:.0f}%)\n\n"
        f"📊 <b>Top categorías de gasto</b>\n{top_lines}",
        parse_mode=ParseMode.HTML)


async def cmd_split(update,ctx):
    text=update.effective_message.text.strip()
    import re
    m=re.match(r"/split\s+(\d+([.,]\d{1,2})?)\s*(.*)", text)
    if not m:
        return await update.effective_message.reply_text("Uso: /split &lt;cantidad&gt; @usuario1 @usuario2 ... [descripción]\n\nEjemplo: /split 60 @juan @maria cena", parse_mode=ParseMode.HTML)
    amt=float(m.group(1).replace(",","."))
    rest=m.group(3).strip()
    usernames=re.findall(r"@(\w+)",rest)
    desc=re.sub(r"@\w+","",rest).strip()
    if not usernames:
        return await update.effective_message.reply_text("Debes mencionar al menos un @usuario para dividir el gasto.", parse_mode=ParseMode.HTML)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    per_person=amt/(len(usernames)+1)
    exp_data=await db._insert_row("shared_expenses",{"payer_id":uid,"description":desc or "Gasto compartido","total_amount":amt})
    exp_id=exp_data.get("id")
    if not exp_id:
        return await update.effective_message.reply_text("Error al crear el gasto compartido.", parse_mode=ParseMode.HTML)
    await db._insert_row("shared_expense_participants",{"expense_id":exp_id,"user_id":uid,"amount":per_person,"paid":True})
    for uname in usernames:
        if uname.isdigit():
            urow=await db._select_rows("users",columns="id",filters=[("eq","telegram_id",int(uname))],limit=1)
        else:
            urow=[]
        if urow:
            await db._insert_row("shared_expense_participants",{"expense_id":exp_id,"user_id":urow[0]["id"],"amount":per_person,"paid":False})
    lines="\n".join(f"• @{u}: €{per_person:.2f}" for u in usernames)
    await update.effective_message.reply_text(
        f"💸 <b>Gasto compartido</b>\n\n{desc or 'Gasto'}: <b>€{amt:.2f}</b>\nDividido entre {len(usernames)+1} personas (€{per_person:.2f} c/u)\n\n{lines}\n\nUsa /deudas para ver lo que te deben.",
        parse_mode=ParseMode.HTML)


async def cmd_deudas(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    owe_you=[]
    you_owe=[]
    my_expenses=await db._select_rows("shared_expenses",filters=[("eq","payer_id",uid)])
    for exp in my_expenses:
        debs=await db._select_rows("shared_expense_participants",filters=[("eq","expense_id",exp["id"]),("eq","paid",False)])
        for d in debs:
            if d["user_id"]==uid:
                continue
            urow=await db._select_rows("users",columns="telegram_id",filters=[("eq","id",d["user_id"])],limit=1)
            who=str(urow[0]["telegram_id"]) if urow else str(d["user_id"])
            owe_you.append((who,exp["description"] or "Gasto",d["amount"]))
    part_rows=await db._select_rows("shared_expense_participants",filters=[("eq","user_id",uid),("eq","paid",False)])
    for p in part_rows:
        exp=await db._select_rows("shared_expenses",columns="payer_id,description",filters=[("eq","id",p["expense_id"])],limit=1)
        if exp and exp[0]["payer_id"]!=uid:
            payer_row=await db._select_rows("users",columns="telegram_id",filters=[("eq","id",exp[0]["payer_id"])],limit=1)
            who=str(payer_row[0]["telegram_id"]) if payer_row else str(exp[0]["payer_id"])
            you_owe.append((who,exp[0]["description"] or "Gasto",p["amount"]))
    msg="💳 <b>Deudas</b>\n\n"
    if owe_you:
        msg+="<b>Te deben:</b>\n"
        for who,desc,amt in owe_you:
            msg+=f"• De @{who}: €{amt:.2f} — {desc}\n"
    else:
        msg+="<b>Te deben:</b> nada\n"
    msg+="\n"
    if you_owe:
        msg+="<b>Debes:</b>\n"
        for who,desc,amt in you_owe:
            msg+=f"• A @{who}: €{amt:.2f} — {desc}\n"
    else:
        msg+="<b>Debes:</b> nada\n"
    await update.effective_message.reply_text(msg,parse_mode=ParseMode.HTML)


async def cmd_factura(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if not accts:
        return await update.effective_message.reply_text("Debes crear una cuenta primero con /nuevacuenta", parse_mode=ParseMode.HTML)
    await save_session(db,tid,"waiting_bill_name")
    await update.effective_message.reply_text("🧾 <b>Nuevo recordatorio de factura</b>\n\n¿Nombre de la factura?\n(Ejemplo: Luz, Internet, Alquiler)\n\n/cancel para cancelar", parse_mode=ParseMode.HTML)


async def cmd_facturas(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    bills=await (await db.execute("SELECT * FROM bill_reminders WHERE user_id=?",(uid,))).fetchall()
    if not bills:
        return await update.effective_message.reply_text("No tienes recordatorios de facturas. Usa /factura para crear uno.", parse_mode=ParseMode.HTML)
    rows=[(b["name"],f"Día {b['day_of_month']}",f"€{b['amount']:.2f}","✅" if b["enabled"] else "❌") for b in bills]
    tbl=unicode_table(["Factura","Día","Importe","Activa"],rows)
    btns=[(f"🗑 Eliminar {b['name']}",f"delbill_{b['id']}") for b in bills]
    btns.append(("Cerrar","cancel_action"))
    await update.effective_message.reply_text(
        f"🧾 <b>Recordatorios de facturas</b>\n<pre>{h(tbl)}</pre>",
        reply_markup=_kb(btns), parse_mode=ParseMode.HTML)


async def cmd_borrarfactura(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    bills=await (await db.execute("SELECT * FROM bill_reminders WHERE user_id=?",(uid,))).fetchall()
    if not bills:
        return await update.effective_message.reply_text("No tienes facturas para eliminar.", parse_mode=ParseMode.HTML)
    btns=[(f"{b['name']} (Día {b['day_of_month']})",f"delbill_{b['id']}") for b in bills]
    btns.append(("Cancelar","cancel_action"))
    await update.effective_message.reply_text("Selecciona la factura a eliminar:", reply_markup=_kb(btns))


async def cmd_sugerircategoria(update,ctx):
    return await finance_reports.cmd_sugerircategoria(update,ctx)


async def cmd_exportar(update,ctx):
    return await finance_reports.cmd_exportar(update,ctx)


async def cmd_alertas(update,ctx):
    db=await get_db(); tid=update.effective_user.id; await save_session(db,tid,"menu_alertas")
    c=await db.execute("SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?",(tid,))
    alerts=await c.fetchall()
    msg="🔔 <b>Gestion de Alertas de Saldo Bajo</b>\n\n"
    if alerts:
        msg+="<b>Alertas configuradas:</b>\n"
        for a in alerts:
            th_val = "{:.2f}".format(a["threshold"])
            msg+=f"{'✅' if a['enabled'] else '❌'} {h(a['name'])}: €{h(th_val)}\n"
    else: msg+="No tienes alertas configuradas.\n"
    msg+="\nOpciones:\n/agregaralerta - Agregar nueva alerta\n/borraralerta - Eliminar alerta\n/cancel - Cancelar"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_agregar_alerta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if not accts: return await update.effective_message.reply_text("No tienes cuentas. Crea una primero con /nuevacuenta", parse_mode=ParseMode.HTML)
    await save_session(db,tid,"waiting_alert_account",{"accounts":[{"id":a["id"],"name":a["name"]} for a in accts]})
    await update.effective_message.reply_text("Selecciona la cuenta:", reply_markup=_acct_kb(accts,"alert_acc",None))


async def cmd_borrar_alerta(update,ctx):
    db=await get_db(); tid=update.effective_user.id
    c=await db.execute("SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?",(tid,))
    alerts=await c.fetchall()
    if not alerts: return await update.effective_message.reply_text("No tienes alertas para eliminar.")
    btns=[(f"{a['name']} — €{a['threshold']:.2f}",f"del_alert_{a['id']}") for a in alerts]
    btns.append(("Cancelar","cancel_action"))
    await update.effective_message.reply_text("Selecciona la alerta a eliminar:", reply_markup=_kb(btns))


async def cmd_reset(update,ctx):
    await update.effective_message.reply_text(
        "⚠️ <b>ATENCION</b>\n\nEsto borrara TODOS tus datos:\n• Cuentas\n• Transacciones\n• Gastos recurrentes\n• Alertas\n• Redondeo\n\nEsta accion NO se puede deshacer.\n\n¿Confirmas?",
        reply_markup=_kb([("✅ Si, borrar TODO","reset_confirm"),("❌ Cancelar","cancel_action")]), parse_mode=ParseMode.HTML)

