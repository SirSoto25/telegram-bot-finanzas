"""Text input / flow handlers for the finance bot."""
import json
from datetime import datetime

from telegram.constants import ParseMode

from _env import get_db
from finance_shared import (
    ACCOUNT_TYPE_MAP, CATEGORY_KBD_ITEMS, CATEGORY_MAP, FREQ_KBD_ITEMS,
    FREQ_MAP, MONTHS_ES, TYPE_KBD_ITEMS,
    _cb_suffix_int, _cb_suffix_text, _smart_category_suggestion,
    h, parse_amount,
)
from finance_db import DBIntegrityError, _tx_wrap
from finance_state import (
    _check_session_expiry, clear_session, get_accounts, get_or_create_user,
    get_roundup, get_session, save_session,
)
from finance_ui import _acct_kb, _kb, multi_kb
from finance_notifications import _check_budget_warning


async def _ht_acct_name(db,tid,uid,text,sdata,update,ctx):
    sdata["accountName"]=text; await save_session(db,tid,"waiting_account_type",sdata)
    await update.effective_message.reply_text("Selecciona el tipo de cuenta:", reply_markup=multi_kb(TYPE_KBD_ITEMS,"type",cols=4,extra=None))

async def _ht_acct_balance(db,tid,uid,text,sdata,update,ctx):
    bal=parse_amount(text)
    if bal is None: return await update.effective_message.reply_text("Cantidad invalida. Intenta de nuevo.", parse_mode=ParseMode.HTML)
    try:
        await db.execute("INSERT INTO accounts(user_id,name,type,balance) VALUES(?,?,?,?)",(uid,sdata["accountName"],sdata["accountType"],bal))
        await db.commit(); await clear_session(db,tid)
        await update.effective_message.reply_text(f"✅ Cuenta <b>{h(sdata['accountName'])}</b> creada correctamente con saldo €{h(f'{bal:.2f}')}", parse_mode=ParseMode.HTML)
    except DBIntegrityError:
        await clear_session(db,tid); await update.effective_message.reply_text("Error al crear la cuenta. Probablemente ya existe una con ese nombre.", parse_mode=ParseMode.HTML)

async def _ht_expense_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.effective_message.reply_text("Cantidad invalida. Intenta de nuevo.", parse_mode=ParseMode.HTML)
    if amt <= 0: return await update.effective_message.reply_text("La cantidad debe ser positiva.", parse_mode=ParseMode.HTML)
    sdata["expenseAmount"]=amt
    await save_session(db,tid,"waiting_expense_category",sdata)
    await update.effective_message.reply_text(f"Gasto: €{h(f'{amt:.2f}')}\n\nSelecciona la categoria:", reply_markup=multi_kb(CATEGORY_KBD_ITEMS,"cat",cols=2,extra=None), parse_mode=ParseMode.HTML)

async def _ht_expense_date_custom(db,tid,uid,text,sdata,update,ctx):
    try: dt=datetime.strptime(text,"%d/%m/%Y"); sdata["expenseDate"]=dt.isoformat()
    except ValueError: return await update.effective_message.reply_text("Formato invalido. Usa DD/MM/AAAA.\n\n/cancel para cancelar")
    accts=await get_accounts(db,uid); await save_session(db,tid,"waiting_expense_account",sdata)
    exp_amt_c="{:.2f}".format(sdata.get('expenseAmount',0))
    await update.effective_message.reply_text(f"Gasto: €{h(exp_amt_c)} en {h(sdata.get('expenseCategory',''))}\n\nSelecciona la cuenta:",reply_markup=_acct_kb(accts,"exp_acc",None), parse_mode=ParseMode.HTML)

async def _ht_income_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.effective_message.reply_text("Cantidad invalida. Intenta de nuevo.", parse_mode=ParseMode.HTML)
    if amt <= 0: return await update.effective_message.reply_text("La cantidad debe ser positiva.", parse_mode=ParseMode.HTML)
    sdata["incomeAmount"]=amt; await save_session(db,tid,"waiting_income_concept",sdata)
    await update.effective_message.reply_text(f"Ingreso: €{h(f'{amt:.2f}')}\n\n¿Cual es el concepto?\n(Ejemplo: Freelance, Regalo, Bonificacion)\n\n/cancel para cancelar", parse_mode=ParseMode.HTML)

async def _ht_income_concept(db,tid,uid,text,sdata,update,ctx):
    sdata["incomeConcept"]=text; accts=await get_accounts(db,uid)
    await save_session(db,tid,"waiting_income_account",sdata)
    inc_amt="{:.2f}".format(sdata.get('incomeAmount',0))
    await update.effective_message.reply_text(f"Ingreso: €{h(inc_amt)}\nConcepto: {h(text)}\n\nSelecciona la cuenta donde ingresa el dinero:",reply_markup=_acct_kb(accts,"inc_acc",None), parse_mode=ParseMode.HTML)

async def _ht_expense_note(db,tid,uid,text,sdata,update,ctx):
    if text == "/saltar":
        note = ""
    else:
        note = text
    await _finalize_expense_with_note(db, tid, uid, sdata, note, update)

async def _ht_income_note(db,tid,uid,text,sdata,update,ctx):
    if text == "/saltar":
        note = ""
    else:
        note = text
    await _finalize_income_with_note(db, tid, uid, sdata, note, update)

async def _ht_transfer_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.effective_message.reply_text("Cantidad invalida.")
    if amt<=0: return await update.effective_message.reply_text("La cantidad debe ser positiva.")
    fid=sdata["from_id"]; tid_dst=sdata["to_id"]
    src=await (await db.execute("SELECT * FROM accounts WHERE id=?",(fid,))).fetchone()
    dst=await (await db.execute("SELECT * FROM accounts WHERE id=?",(tid_dst,))).fetchone()
    if src["balance"]<amt:
        bal_val="{:.2f}".format(src["balance"])
        return await update.effective_message.reply_text(f"❌ Saldo insuficiente en <b>{h(src['name'])}</b> (€{h(bal_val)})",parse_mode=ParseMode.HTML)
    await _tx_wrap(db,[
        ("UPDATE accounts SET balance=balance-? WHERE id=?",(amt,fid)),
        ("UPDATE accounts SET balance=balance+? WHERE id=?",(amt,tid_dst)),
        ("INSERT INTO transactions(user_id,account_id,amount,type,category,description,linked_account_id) VALUES(?,?,?,'TRANSFERENCIA','Transferencia',?,?)",(uid,fid,amt,f"Transferencia a {dst['name']}",tid_dst)),
    ])
    await clear_session(db,tid)
    await update.effective_message.reply_text(f"✅ Transferencia realizada\n💱 €{h(f'{amt:.2f}')}\n📤 {h(src['name'])} → 📥 {h(dst['name'])}",parse_mode=ParseMode.HTML)

async def _ht_recurring_name(db,tid,uid,text,sdata,update,ctx):
    sdata["recurringName"]=text; await save_session(db,tid,"waiting_recurring_amount",sdata)
    await update.effective_message.reply_text(f"Nombre: {h(text)}\n\n¿Cual es la cantidad?\n(Formato: cantidad)\n\n/cancel para cancelar", parse_mode=ParseMode.HTML)

async def _ht_recurring_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.effective_message.reply_text("Cantidad invalida.")
    if amt <= 0: return await update.effective_message.reply_text("La cantidad debe ser positiva.")
    sdata["recurringAmount"]=amt; await save_session(db,tid,"waiting_recurring_frequency",sdata)
    await update.effective_message.reply_text(f"Gasto recurrente: {h(sdata.get('recurringName',''))}\nMonto: €{h(f'{amt:.2f}')}\n\nSelecciona la frecuencia:",reply_markup=multi_kb(FREQ_KBD_ITEMS,"freq",cols=2,extra=None), parse_mode=ParseMode.HTML)

async def _ht_recurring_income_name(db,tid,uid,text,sdata,update,ctx):
    sdata["recurringIncomeName"] = text
    await save_session(db,tid,"waiting_recurring_income_amount",sdata)
    await update.effective_message.reply_text(f"Nombre: {h(text)}\n\n¿Cual es la cantidad?\n(Formato: cantidad)\n\n/cancel para cancelar", parse_mode=ParseMode.HTML)

async def _ht_recurring_income_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.effective_message.reply_text("Cantidad invalida.")
    if amt <= 0: return await update.effective_message.reply_text("La cantidad debe ser positiva.")
    sdata["recurringIncomeAmount"] = amt
    await save_session(db,tid,"waiting_recurring_income_frequency",sdata)
    await update.effective_message.reply_text(
        f"Ingreso recurrente: {h(sdata.get('recurringIncomeName',''))}\nMonto: €{h(f'{amt:.2f}')}\n\nSelecciona la frecuencia:",
        reply_markup=multi_kb(FREQ_KBD_ITEMS,"freqinc",cols=2,extra=None)
    )

async def _ht_alert_threshold(db,tid,uid,text,sdata,update,ctx):
    th=parse_amount(text)
    if th is None: return await update.effective_message.reply_text("Cantidad invalida.")
    if th <= 0: return await update.effective_message.reply_text("La cantidad debe ser positiva.")
    await db.execute("INSERT OR REPLACE INTO low_balance_alerts(telegram_id,account_id,threshold,enabled) VALUES(?,?,?,1)",(tid,sdata["account_id"],th))
    await db.commit(); await clear_session(db,tid)
    await update.effective_message.reply_text(f"✅ Alerta configurada\n🔔 Se te notificara cuando el saldo sea menor a €{h(f'{th:.2f}')}", parse_mode=ParseMode.HTML)

async def _ht_budget_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.effective_message.reply_text("Cantidad invalida.")
    if amt<=0: return await update.effective_message.reply_text("La cantidad debe ser positiva.")
    cat_key=sdata["budgetCategory"]; cat_name=CATEGORY_MAP[cat_key]
    now=datetime.now(); month=f"{now.year}-{now.month:02d}"
    await db.execute("INSERT OR REPLACE INTO budgets(user_id,category,amount,month) VALUES(?,?,?,?)",(uid,cat_name,amt,month))
    await db.commit(); await clear_session(db,tid)
    await update.effective_message.reply_text(f"✅ Presupuesto configurado\n📊 {h(cat_name)}: €{h(f'{amt:.2f}')} para {h(MONTHS_ES[now.month])} {h(str(now.year))}", parse_mode=ParseMode.HTML)

async def _ht_goal_name(db,tid,uid,text,sdata,update,ctx):
    sdata["goalName"]=text; await save_session(db,tid,"waiting_goal_target",sdata)
    await update.effective_message.reply_text(f"Meta: {h(text)}\n\n¿Cual es el monto objetivo?\n(Formato: cantidad)\n\n/cancel para cancelar", parse_mode=ParseMode.HTML)

async def _ht_goal_target(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None or amt<=0: return await update.effective_message.reply_text("Cantidad invalida. Debe ser positiva.")
    sdata["goalTarget"]=amt; await save_session(db,tid,"waiting_goal_deadline",sdata)
    await update.effective_message.reply_text(f"Meta: {h(sdata.get('goalName',''))} — Objetivo: €{h(f'{amt:.2f}')}\n\n¿Fecha limite? (DD/MM/AAAA o /saltar si no hay)",parse_mode=ParseMode.HTML)

async def _ht_goal_deadline(db,tid,uid,text,sdata,update,ctx):
    dl=None
    if text!="/saltar":
        try: dl=datetime.strptime(text,"%d/%m/%Y").isoformat()
        except ValueError: return await update.effective_message.reply_text("Formato invalido. Usa DD/MM/AAAA o /saltar")
    await db.execute("INSERT INTO savings_goals(user_id,name,target_amount,deadline) VALUES(?,?,?,?)",(uid,sdata["goalName"],sdata["goalTarget"],dl))
    await db.commit(); await clear_session(db,tid)
    gt_amt="{:.2f}".format(sdata["goalTarget"])
    await update.effective_message.reply_text(f"✅ Meta creada\n🎯 {h(sdata['goalName'])}: €{h(gt_amt)}",parse_mode=ParseMode.HTML)

async def _ht_aportar_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None or amt<=0: return await update.effective_message.reply_text("Cantidad invalida.")
    gid=sdata["goalId"]
    await db.execute("UPDATE savings_goals SET current_amount=current_amount+? WHERE id=? AND user_id=?",(amt,gid,uid))
    await db.commit(); await clear_session(db,tid)
    await update.effective_message.reply_text(f"✅ Aporte registrado\n🎯 +€{h(f'{amt:.2f}')} a tu meta",parse_mode=ParseMode.HTML)

async def _ht_menu_recurrente(db,tid,uid,text,sdata,update,ctx):
    if text=="/agregarrecurrente":
        await save_session(db,tid,"waiting_recurring_name")
        await update.effective_message.reply_text("¿Cual es el nombre del gasto recurrente?\n(Ejemplo: Netflix, Seguro del coche)\n\n/cancel para cancelar")

async def _ht_menu_alertas(db,tid,uid,text,sdata,update,ctx):
    if text=="/agregaralerta":
        accts=await get_accounts(db,uid)
        if not accts: return await update.effective_message.reply_text("Crea una cuenta primero con /nuevacuenta")
        await update.effective_message.reply_text("Selecciona la cuenta:", reply_markup=_acct_kb(accts,"alert_acc",None))
    elif text=="/borraralerta":
        c=await db.execute("SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?",(tid,))
        alerts=await c.fetchall()
        if not alerts: return await update.effective_message.reply_text("No tienes alertas para eliminar.")
        btns=[(f"{a['name']} — €{a['threshold']:.2f}",f"del_alert_{a['id']}") for a in alerts]
        btns.append(("Cancelar","cancel_action"))
        await update.effective_message.reply_text("Selecciona la alerta a eliminar:", reply_markup=_kb(btns))

async def _ht_menu_ingresorec(db,tid,uid,text,sdata,update,ctx):
    await clear_session(db,tid)
    await update.effective_message.reply_text("Usa /agregaringresorecurrente para agregar un ingreso recurrente.",parse_mode=ParseMode.HTML)

_TEXT_HANDLERS = {
    "waiting_account_name": _ht_acct_name,
    "waiting_account_balance": _ht_acct_balance,
    "waiting_expense_amount": _ht_expense_amount,
    "waiting_expense_note": _ht_expense_note,
    "waiting_expense_date_custom": _ht_expense_date_custom,
    "waiting_income_amount": _ht_income_amount,
    "waiting_income_concept": _ht_income_concept,
    "waiting_income_note": _ht_income_note,
    "waiting_transfer_amount": _ht_transfer_amount,
    "waiting_recurring_name": _ht_recurring_name,
    "waiting_recurring_amount": _ht_recurring_amount,
    "waiting_recurring_income_name": _ht_recurring_income_name,
    "waiting_recurring_income_amount": _ht_recurring_income_amount,
    "waiting_alert_threshold": _ht_alert_threshold,
    "waiting_budget_amount": _ht_budget_amount,
    "waiting_goal_name": _ht_goal_name,
    "waiting_goal_target": _ht_goal_target,
    "waiting_goal_deadline": _ht_goal_deadline,
    "waiting_aportar_amount": _ht_aportar_amount,
    "menu_recurrente": _ht_menu_recurrente,
    "menu_alertas": _ht_menu_alertas,
    "menu_ingresorec": _ht_menu_ingresorec,
}

async def handle_text(update,ctx):
    db=await get_db(); tid=update.effective_user.id; text=update.effective_message.text.strip()
    if text.startswith("/cancel"):
        await clear_session(db,tid)
        return await update.effective_message.reply_text("✅ Operacion cancelada.", parse_mode=ParseMode.HTML)
    s=await get_session(db,tid)
    if await _check_session_expiry(db,tid):
        return await update.effective_message.reply_text("⏰ Sesion expirada. Usa /start para comenzar de nuevo.")
    if not s: return await update.effective_message.reply_text("Usa /start para comenzar o /help para ver los comandos disponibles.")
    uid=await get_or_create_user(db,tid); state=s["state"]
    try:
        sdata=json.loads(s["data"]) if s["data"] else {}
    except (json.JSONDecodeError, TypeError):
        await clear_session(db,tid)
        return await update.effective_message.reply_text("⚠️ Datos de sesion corruptos. Usa /start para reiniciar.", parse_mode=ParseMode.HTML)
    handler=_TEXT_HANDLERS.get(state)
    if handler: return await handler(db,tid,uid,text,sdata,update,ctx)
    await update.effective_message.reply_text("Usa /start para comenzar o /help para ver los comandos disponibles.")

_TEXT_HANDLERS = {
    "waiting_account_name": _ht_acct_name,
    "waiting_account_balance": _ht_acct_balance,
    "waiting_expense_amount": _ht_expense_amount,
    "waiting_expense_note": _ht_expense_note,
    "waiting_expense_date_custom": _ht_expense_date_custom,
    "waiting_income_amount": _ht_income_amount,
    "waiting_income_concept": _ht_income_concept,
    "waiting_income_note": _ht_income_note,
    "waiting_transfer_amount": _ht_transfer_amount,
    "waiting_recurring_name": _ht_recurring_name,
    "waiting_recurring_amount": _ht_recurring_amount,
    "waiting_recurring_income_name": _ht_recurring_income_name,
    "waiting_recurring_income_amount": _ht_recurring_income_amount,
    "waiting_alert_threshold": _ht_alert_threshold,
    "waiting_budget_amount": _ht_budget_amount,
    "waiting_goal_name": _ht_goal_name,
    "waiting_goal_target": _ht_goal_target,
    "waiting_goal_deadline": _ht_goal_deadline,
    "waiting_aportar_amount": _ht_aportar_amount,
    "menu_recurrente": _ht_menu_recurrente,
    "menu_alertas": _ht_menu_alertas,
    "menu_ingresorec": _ht_menu_ingresorec,
}


