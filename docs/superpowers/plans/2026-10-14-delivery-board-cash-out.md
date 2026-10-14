# Delivery Board: Cash Out source and Budget Status basis

**Date:** 2026-10-14
**Area:** pmo (Project Management -> Delivery Board), finance

## Decision

- **Cash Out** is money paid to vendors, read from Finance -> Cash Outflow
  (`finance.CashOutflowRow`): staged payments dated on or before today, ex-VAT
  (`amount`, not `total_amount`). It used to be procurement's committed POs.
  Changed at the lead's request so the board reuses the Finance module.
- **Budget Status** (Over / Under / Within / No budget) compares the
  finance-approved budget with **committed purchase orders**
  (`procurement.budget_status.commitment`: issued and later, ex-VAT, cancelled
  excluded) - the same rule as procurement's budget screens.
- **Cash In - Cash Out** and Financial Status stay cash-based.

## Why Budget Status is not based on Cash Out

Finance types payment dates on the cash outflow schedule, often late. Until
they do, Cash Out is zero, and a project committed far past its budget would
read "Under Budget" in green. "Over Budget" on a management board means the
commitment is past the budget, not the payments (code review on the
delivery-board-financials PR).

## Consequences

- Cash Out only moves when finance stages payment dates; it is not a
  commitment figure.
- The board prefetches `cash_outflow_rows` and `purchase_orders` in
  `board_rows`, so query count stays flat with board size.
- Tests: `pmo/tests_progress.py`, `pmo/tests_board_cash_out.py`.
