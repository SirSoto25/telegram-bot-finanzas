try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except Exception:
    class InlineKeyboardButton:
        def __init__(self, text, callback_data=None):
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard


def _kb(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for t, d in buttons])


def _acct_kb(accounts, prefix, extra=None):
    btns = [(f"{a['name']} (€{a['balance']:.2f})", f"{prefix}_{a['id']}") for a in accounts]
    if extra:
        btns.extend(extra)
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for t, d in btns])


def multi_kb(items, prefix, cols=2, extra=None):
    rows = [[InlineKeyboardButton(label, callback_data=f"{prefix}_{key}")] for label, key in items]
    if extra:
        rows.extend([[InlineKeyboardButton(label, callback_data=cd)] for label, cd in extra])
    return InlineKeyboardMarkup(rows)


def _confirm_kb(confirm_cb, current_text):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Si, confirmar", callback_data=confirm_cb)],
            [InlineKeyboardButton("❌ No, cancelar", callback_data="cancel_action")],
        ]
    )
