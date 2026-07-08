# Referencia de comandos y estados de sesión

## Comandos principales

| Comando | Propósito |
|---|---|
| `/start` | Inicializa usuario y limpia sesión |
| `/cuentas` | Lista cuentas |
| `/nuevacuenta` | Inicia flujo de alta de cuenta |
| `/gasto` | Inicia flujo de gasto |
| `/ingreso` | Inicia flujo de ingreso |
| `/traspaso` | Inicia flujo de transferencia |
| `/recurrente` | Lista gastos recurrentes |
| `/agregarrecurrente` | Alta de gasto recurrente |
| `/ingresorecurrente` | Lista ingresos recurrentes |
| `/agregaringresorecurrente` | Alta de ingreso recurrente |
| `/presupuesto` | Ver presupuestos |
| `/presupuestoset` | Alta/edición de presupuesto |
| `/metas` | Listado de metas |
| `/nuevameta` | Alta de meta |
| `/aportarmeta` | Aporte a meta |
| `/alertas` | Gestión de alertas |
| `/exportar` | Exportación de datos |

## Estados conversacionales (selección)

| Estado | Handler |
|---|---|
| `waiting_account_name` | `_ht_acct_name` |
| `waiting_account_balance` | `_ht_acct_balance` |
| `waiting_expense_amount` | `_ht_expense_amount` |
| `waiting_expense_category` | `_hfc_expense_cat` |
| `waiting_expense_date` | `_hfc_expense_date` |
| `waiting_expense_account` | `_hfc_expense_acc` |
| `waiting_income_amount` | `_ht_income_amount` |
| `waiting_income_concept` | `_ht_income_concept` |
| `waiting_income_account` | `_hfc_income_acc` |
| `waiting_transfer_amount` | `_ht_transfer_amount` |
| `waiting_recurring_name` | `_ht_recurring_name` |
| `waiting_recurring_amount` | `_ht_recurring_amount` |
| `waiting_recurring_frequency` | `_hfc_rec_freq` |
| `waiting_recurring_category` | `_hfc_rec_cat` |
| `waiting_recurring_account` | `_hfc_rec_acc` |
| `waiting_recurring_income_name` | `_ht_recurring_income_name` |
| `waiting_recurring_income_amount` | `_ht_recurring_income_amount` |
| `waiting_recurring_income_frequency` | `_hfc_rec_income_freq` |
| `waiting_recurring_income_account` | `_hfc_rec_income_acc` |
| `waiting_budget_amount` | `_ht_budget_amount` |
| `waiting_goal_name` | `_ht_goal_name` |
| `waiting_goal_target` | `_ht_goal_target` |
| `waiting_goal_deadline` | `_ht_goal_deadline` |

