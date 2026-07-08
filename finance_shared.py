import calendar
import html
import re
from datetime import datetime, timedelta

CATEGORY_MAP = {"1":"Comida","2":"Transporte","3":"Suscripciones","4":"Coche","5":"Entretenimiento","6":"Vivienda","7":"Utilidades","8":"Otros"}
ACCOUNT_TYPE_MAP = {"1":"NOMINA","2":"AHORROS","3":"INVERSION","4":"CRIPTO"}
FREQ_MAP = {"1":"SEMANAL","2":"MENSUAL","3":"TRIMESTRAL","4":"ANUAL"}
MONTHS_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

CATEGORY_KBD_ITEMS = [
    ("🍕 Comida","1"),("🚌 Transporte","2"),("📺 Suscripciones","3"),("🚗 Coche","4"),
    ("🎮 Entretenimiento","5"),("🏠 Vivienda","6"),("💡 Utilidades","7"),("🏷️ Otros","8"),
]
TYPE_KBD_ITEMS = [("🏦 NOMINA","1"),("💰 AHORROS","2"),("📈 INVERSION","3"),("🪙 CRIPTO","4")]
FREQ_KBD_ITEMS = [("📅 SEMANAL","1"),("📅 MENSUAL","2"),("📅 TRIMESTRAL","3"),("📅 ANUAL","4")]

SESSION_TIMEOUT_MINUTES = 30
SYSTEM_BOT_TELEGRAM_ID = 0

SMART_CATEGORY_RULES = [
    ("Comida", ["supermercado", "mercadona", "lidl", "carrefour", "restaurante", "bar", "comida", "glovo", "uber eats", "takeaway"]),
    ("Transporte", ["metro", "bus", "tren", "taxi", "uber", "cabify", "gasolina", "parking", "peaje", "carga"]),
    ("Suscripciones", ["netflix", "spotify", "disney", "hbo", "prime", "youtube premium", "suscripcion", "subscription"]),
    ("Vivienda", ["alquiler", "hipoteca", "luz", "agua", "gas", "internet", "fibra"]),
    ("Coche", ["itv", "taller", "seguro coche", "mantenimiento", "neumatico", "garage"]),
    ("Entretenimiento", ["cine", "concierto", "juego", "gaming", "teatro", "ocio"]),
    ("Utilidades", ["impuestos", "telefono", "mantenimiento", "seguridad", "software"]),
]

def h(text):
    return html.escape(str(text))

def parse_amount(text):
    try:
        return float(text)
    except ValueError:
        return None

def _cb_suffix_int(data, prefix):
    suffix = data[len(prefix):]
    return int(suffix) if suffix.isdigit() else None

def _cb_suffix_text(data, prefix):
    if not data.startswith(prefix):
        return None
    suffix = data[len(prefix):]
    return suffix if suffix else None

def _extract_tags(text):
    if not text:
        return []
    return [t.lower() for t in re.findall(r"#([A-Za-z0-9_-]+)", text)]

def _smart_category_suggestion(text):
    if not text:
        return None
    lowered = text.lower()
    for category, keywords in SMART_CATEGORY_RULES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None

def _month_shift(dt, months):
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)

def _month_window(dt):
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_day = calendar.monthrange(dt.year, dt.month)[1]
    end = dt.replace(day=end_day, hour=23, minute=59, second=59, microsecond=999999)
    return start, end

def session_is_expired(created_at, timeout_minutes, now=None):
    if created_at is None:
        return False
    if now is None:
        current = datetime.now(created_at.tzinfo) if created_at.tzinfo is not None else datetime.now()
    else:
        current = now
    return current - created_at > timedelta(minutes=timeout_minutes)
