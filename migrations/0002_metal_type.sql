-- Migration 0002: Add multi-metal support (Gold, Silver, Both)
-- Adds metal_type to products and inventory, silver_rate to sales

ALTER TABLE product_master ADD COLUMN metal_type TEXT DEFAULT 'gold';
ALTER TABLE stock_inventory ADD COLUMN metal_type TEXT DEFAULT 'gold';
ALTER TABLE sales_history ADD COLUMN silver_rate REAL DEFAULT 0;
