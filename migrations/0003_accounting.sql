-- Migration 0003: Accounting engine enhancements
-- Adds reconciliation columns to voucher lines

ALTER TABLE cash_voucher_lines ADD COLUMN reconciled INTEGER DEFAULT 0;
ALTER TABLE cash_voucher_lines ADD COLUMN reconciled_date TEXT DEFAULT '';
