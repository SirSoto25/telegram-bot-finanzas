# Architecture

## Overview

The bot follows a layered architecture with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram API                          │
└─────────────────────┬───────────────────────────────────┘
                      │ webhook POST
                      ▼
┌─────────────────────────────────────────────────────────┐
│           Flask WSGI (bot_pythonanywhere.py)             │
│  ┌──────────────────────────────────────────────────┐   │
│  │           PTB Application (python-telegram-bot)   │   │
│  │  ┌──────────┬──────────────┬──────────────────┐  │   │
│  │  │ Commands │  Callbacks   │   Text Messages  │  │   │
│  │  │(commands │ (callbacks   │    (flows.py)     │  │   │
│  │  │   .py)   │    .py)      │                  │  │   │
│  │  └────┬─────┴──────┬───────┴────────┬─────────┘  │   │
│  │       │            │                │             │   │
│  │       ▼            ▼                ▼             │   │
│  │  ┌─────────────────────────────────────────────┐  │   │
│  │  │            Business Logic                    │  │   │
│  │  │  finance_analytics.py (reports, predictions) │  │   │
│  │  │  finance_notifications.py (alerts, budget)  │  │   │
│  │  │  finance_state.py (sessions, user mgmt)     │  │   │
│  │  │  finance_shared.py (constants, utilities)   │  │   │
│  │  │  finance_ui.py (keyboard builders)          │  │   │
│  │  └────────────────────┬────────────────────────┘  │   │
│  │                       │                            │   │
│  │                       ▼                            │   │
│  │  ┌─────────────────────────────────────────────┐  │   │
│  │  │         Data Access Layer                     │  │   │
│  │  │  finance_db.py (SupabaseDB + SQL parser)     │  │   │
│  │  └────────────────────┬────────────────────────┘  │   │
│  └───────────────────────┼───────────────────────────┘   │
└──────────────────────────┼───────────────────────────────┘
                           │ HTTPS
                           ▼
                    ┌──────────────┐
                    │   Supabase   │
                    └──────────────┘
```

## Module Details

### `bot_pythonanywhere.py` — Entry Point (157 lines)
- Flask WSGI app for PythonAnywhere deployment
- Webhook receiver (`/<token>`)
- PTB Application factory (`_create_ptb_app`)
- Job scheduling: recurring reminders (hourly), weekly panel (Mondays)
- Handler registration via `handlers_registry`

### `commands.py` — Command Handlers (809 lines)
55 handlers for all `/command` invocations. Each handler:
1. Gets DB connection via `await get_db()`
2. Validates prerequisites (e.g., user has accounts)
3. Either shows data directly or starts a multi-step flow by saving session state

### `callbacks.py` — Callback Query Handlers (496 lines)
Handles inline keyboard button presses:
- `handle_menu_callback`: Main menu dispatcher → delegates to `cmd_*` functions
- `handle_callback`: Account/recurring/alert deletion, transfers, redondeo, reset
- `handle_flow_callback`: Multi-step flow selections (category picker, account picker, date, frequency)
- `_FLOW_CALLBACK_MAP`: Maps session state → `_hfc_*` handler functions

### `flows.py` — Text Input Handlers (257 lines)
Multi-step conversation state machine:
- `handle_text`: Entry point for text messages during active sessions
- `_TEXT_HANDLERS`: Dict mapping session state → `_ht_*` handler
- Each handler validates input, stores partial data in session, advances to next state
- `_finalize_expense_with_note` / `_finalize_income_with_note`: Commit transactions
- JSON parsing with error recovery for corrupted session data

### `finance_db.py` — Database Adapter (738 lines)
- `SupabaseDB`: Translates SQL syntax to Supabase REST API calls
- `SupabaseCursor`: Async cursor wrapper around row data
- `execute(sql, params)`: Regex-based SQL parser extracts operation, table, columns, WHERE conditions, aggregates
- Operator mapping: `=` → `eq`, `!=` → `neq`, `>=` → `gte`, etc.
- Handles JOINs via post-processing, aggregates (SUM/COUNT) client-side
- Retry with exponential backoff for transient network errors
- `_tx_wrap`: Transaction wrapper (BEGIN/ROLLBACK are no-ops on Supabase)
- `migrate_legacy_sqlite`: One-time migration from legacy SQLite

### `finance_shared.py` — Shared Utilities (88 lines)
- Constants: `CATEGORY_MAP`, `ACCOUNT_TYPE_MAP`, `FREQ_MAP`, `MONTHS_ES`
- `h(text)`: HTML escape wrapper
- `parse_amount(text)`: Float parser with validation
- `parse_quick_expense(text)`: Parse `/g <amount> [desc]` input
- `_cb_suffix_int/str`: Parse callback data prefixes
- `_extract_tags`: Extract `#tag` from text
- `_smart_category_suggestion`: Keyword-based category inference
- `end_of_month(dt)`: Calculate last datetime of month
- `_month_window(dt)`: Start/end bounds of month
- `session_is_expired`: Check session timeout

### `finance_state.py` — Session & User State (65 lines)
- `get_or_create_user`: Upsert user by Telegram ID
- `save_session` / `get_session` / `clear_session`: CRUD for session state
- `_check_session_expiry`: Timeout check (30 minutes)
- `get_accounts` / `get_roundup`: Common queries
- `get_system_state` / `save_system_state`: Bot-level metadata

### `finance_ui.py` — UI Builders (38 lines)
- `_kb(buttons)`: Inline keyboard from list of (label, callback) tuples
- `_acct_kb(accounts, prefix)`: Account selector keyboard
- `multi_kb(items, prefix)`: Multi-column keyboard
- `_confirm_kb(confirm_cb, current_text)`: Yes/No confirmation keyboard

### `finance_analytics.py` — Analytics (536 lines)
- `get_monthly_tx`: Monthly income/expense aggregation
- `bar_chart` / `trend_chart`: ASCII chart generators
- `unicode_table`: ASCII table formatter
- `_build_anomalies`: Statistical anomaly detection
- `_build_financial_snapshot`: Current financial state summary
- `_format_panel_text`: Formatted panel output
- `predict_expenses` / `savings_recs`: Predictions and recommendations
- `get_net_worth_history`: Net worth evolution over N months
- `get_yoy_comparison`: Year-over-year category comparison
- `get_burn_rate`: Daily spend rate and days until balance exhausted
- `get_savings_rate`: Monthly savings percentage over N months
- `get_advice`: Personalized financial tips from data patterns
- `get_50_30_20`: Spending breakdown vs 50/30/20 rule
- `get_goal_projections`: Time-to-completion for savings goals
- `get_phantom_expenses`: Duplicate/recurring expense detection
- `check_and_award_achievements`: Gamification achievement system (7 achievements)
- `get_streak` / `update_streak`: Savings streak tracking

### `finance_notifications.py` — Notifications (59 lines)
- `check_alerts`: Look for accounts below alert threshold
- `_check_budget_warning`: Budget threshold warnings
- `_expense_ask_account`: Account selection helper

### `finance_reports.py` — Report Delegates (197 lines)
- `cmd_resumen`: Monthly summary with inline buttons for charts/recommendations
- `cmd_stats`: 6-month statistics table
- `cmd_tendencia`: 12-month trend charts
- `cmd_panel`: Snapshot + anomalies panel
- `cmd_anomalias`: Anomaly detection report
- `cmd_forecast`: End-of-month projection
- `cmd_tags`: Tag frequency report
- `cmd_sugerircategoria`: Category suggestion
- `cmd_exportar`: CSV export

### `handlers_registry.py` — Handler Registration
- `register_handlers(application, handlers)`: Wires 55 command handlers, 6 callback handlers, 1 message handler
- Fallback classes for testing without PTB installed

### `_env.py` — Environment (8 lines)
- Shared `get_db()` and `SUPABASE_URL`/`SUPABASE_KEY` for all modules

## Scheduled Jobs

Four background jobs run via PTB's `JobQueue`:

| Job | Interval | First run | Description |
|-----|----------|-----------|-------------|
| `check_recurring_reminders` | 1 hour | +10s | Checks for recurring expenses due within 24h and sends reminders |
| `maybe_send_weekly_panel` | 1 hour | +60s | Sends financial panel to all users on Mondays |
| `send_daily_summaries` | 1 hour | +30s | Sends daily balance summary to users with `/resumendiario on` |
| `check_bill_reminders` | 1 hour | +120s | Checks for bill reminders due today and sends notifications |

## New Features (post-refactor)

### Quick expense (`/g`)
Single-message expense registration: `/g 25.50 mercadona` parses amount, infers category via keyword matching, and if the user has multiple accounts, shows an inline keyboard to select one.

### Coaching suite
- `/consejo`: Analyzes savings rate, YoY trends, burn rate, and anomaly data to generate personalized tips
- `/regla`: Visual breakdown of spending into needs/wants/savings vs the 50/30/20 rule
- `/proyeccion`: For each savings goal, estimates months to completion based on average monthly savings
- `/fantasmas`: Scans last 60 days of expenses for repeated descriptions, flagging potential money leaks

### Notifications
- `/resumendiario on|off`: Toggle daily 8am balance summary
- `/factura` flow: Create monthly bill reminders (name, day, amount) with automatic notifications

### Gamification
- `/racha`: Tracks consecutive months with positive savings
- `/logros`: 7 achievements (first expense, 100 transactions, streak milestones, etc.)
- `/resumenanual`: Yearly review with top spending categories

### Shared expenses
- `/split 60 @juan @maria`: Divides an expense among mentioned users
- `/deudas`: Shows who owes you and who you owe

## State Machine

Multi-step flows use a manual state machine pattern:

```
cmd_gasto() ──► session_states: "waiting_expense_amount"
                    │
        user types "45.50"
                    │
                    ▼
  handle_text() ──► _ht_expense_amount()
                    │ validates, saves to session
                    │
                    ▼
              session_states: "waiting_expense_category"
                    │
        user taps "Comida" button
                    │
                    ▼
  handle_flow_callback() ──► _hfc_expense_cat()
                             │ saves, advances
                             ...
                             ▼
                    _finalize_expense_with_note()
                    │ INSERT transaction, UPDATE balance
                    │ clear_session()
                    ▼
                  "✅ Gasto registrado"
```

States and their handlers:
| State | Handler | Type |
|-------|---------|------|
| `waiting_account_name` | `_ht_acct_name` | Text |
| `waiting_account_type` | `_hfc_acct_type` | Callback |
| `waiting_account_balance` | `_ht_acct_balance` | Text |
| `waiting_expense_amount` | `_ht_expense_amount` | Text |
| `waiting_expense_category` | `_hfc_expense_cat` | Callback |
| `waiting_expense_date` | `_hfc_expense_date` | Callback |
| `waiting_expense_account` | `_hfc_expense_acc` | Callback |
| `waiting_expense_note` | `_ht_expense_note` | Text |
| `waiting_income_amount` | `_ht_income_amount` | Text |
| `waiting_income_concept` | `_ht_income_concept` | Text |
| `waiting_income_account` | `_hfc_income_acc` | Callback |
| `waiting_income_note` | `_ht_income_note` | Text |
| ... | ... | ... |

Session timeout: 30 minutes, checked at entry of `handle_text`, `handle_callback`, and `handle_flow_callback`.

## SQL Parser

The `SupabaseDB.execute()` method uses regex-based SQL parsing instead of exact string matching:

```python
def _parse_sql(self, q, p):
    if q.upper().startswith("SELECT"):
        return self._parse_select(q, p)   # extracts table, columns, WHERE, ORDER BY, LIMIT, GROUP BY, aggregates
    elif q.upper().startswith("INSERT"):
        return self._parse_insert(q, p)   # extracts table, columns, VALUES
    elif q.upper().startswith("UPDATE"):
        return self._parse_update(q, p)   # extracts table, SET, WHERE
    elif q.upper().startswith("DELETE"):
        return self._parse_delete(q, p)   # extracts table, WHERE
```

WHERE conditions are split by `AND`, then each condition is parsed as `column op value` where `op` maps to Supabase filters. JOIN queries are handled by querying both tables and joining client-side.

## Deployment

The bot runs on PythonAnywhere as a Flask WSGI application:
- Webhook URL: `https://<user>.pythonanywhere.com/<TOKEN>`
- The `application` object in `bot_pythonanywhere.py` is the WSGI entry point
- PTB Application is lazily created on first request
- Jobs (recurring reminders, weekly panel) run via PTB's `JobQueue`

## Testing

28 tests in 3 files:
- `test_shared_and_analytics.py`: Unit tests for utilities, analytics, notifications, state, UI, report handlers
- `test_command_handlers.py`: Characterization tests for 8 command handlers using patched modules
- `test_flows.py`: Flow state machine tests for session management, expiry, cancellation

Tests use `unittest.mock.patch` to replace DB and state functions with fakes. No real Supabase connection needed.
