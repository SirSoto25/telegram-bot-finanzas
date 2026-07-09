import os
from finance_db import get_db as _finance_get_db

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

async def get_db():
    return await _finance_get_db(SUPABASE_URL, SUPABASE_KEY)
