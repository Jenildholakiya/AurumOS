-- 0000_baseline.sql — channel marker, intentional no-op.
--
-- This file proves the migrations/ channel works end to end: it ships in
-- version.json, downloads beside the app, and DBManager.apply_migrations
-- records it in app_config exactly once. Real schema fixes go in new files
-- named NNNN_what_changed.sql (zero-padded order = apply order). Each file
-- must be idempotent (CREATE TABLE IF NOT EXISTS, ALTER only when the
-- column is missing) because a shop may receive it twice across reinstalls.
SELECT 1;
