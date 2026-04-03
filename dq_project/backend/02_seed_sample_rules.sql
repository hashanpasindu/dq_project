-- ════════════════════════════════════════════════════════════════════════════
-- 🛡️ SAMPLE RULES — Customize table names to match your environment
-- ════════════════════════════════════════════════════════════════════════════
-- Replace catalog.schema.table references with your actual tables.
-- Run after 01_create_tables.sql


-- ── Freshness checks ────────────────────────────────────────────────────
INSERT INTO data_quality.rules (rule_id, rule_name, dataset, rule_type, rule_sql, severity, category, owner, schedule, active, created_by, created_at, updated_at)
VALUES
('R001', 'Orders daily freshness',       'sales.orders',       'freshness',      'SELECT MAX(load_date) >= current_date() - INTERVAL 1 DAY FROM sales.orders',                   'critical', 'sales',     'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),
('R002', 'Customers updated this week',  'master.customers',   'freshness',      'SELECT MAX(updated_at) >= current_date() - INTERVAL 7 DAY FROM master.customers',              'warning',  'master',    'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),
('R003', 'Campaign data freshness',      'marketing.campaigns','freshness',      'SELECT MAX(updated_at) >= current_date() - INTERVAL 1 DAY FROM marketing.campaigns',           'warning',  'marketing', 'marketing-ops',    'daily', true, 'setup', current_timestamp(), current_timestamp()),

-- ── Row count checks ────────────────────────────────────────────────────
('R004', 'Orders not empty today',       'sales.orders',       'row_count',      'SELECT COUNT(*) > 0 FROM sales.orders WHERE load_date = current_date()',                        'critical', 'sales',     'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),
('R005', 'Payments min 100 rows daily',  'sales.payments',     'row_count',      'SELECT COUNT(*) >= 100 FROM sales.payments WHERE load_date = current_date()',                   'warning',  'sales',     'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),

-- ── Null checks ─────────────────────────────────────────────────────────
('R006', 'Order ID never null',          'sales.orders',       'null_check',     'SELECT COUNT(*) = 0 FROM sales.orders WHERE order_id IS NULL',                                 'critical', 'sales',     'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),
('R007', 'Customer email not null',      'master.customers',   'null_check',     'SELECT COUNT(*) = 0 FROM master.customers WHERE email IS NULL',                                'warning',  'master',    'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),

-- ── Uniqueness checks ───────────────────────────────────────────────────
('R008', 'Customer ID unique',           'master.customers',   'uniqueness',     'SELECT COUNT(*) = COUNT(DISTINCT customer_id) FROM master.customers',                           'critical', 'master',    'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),
('R009', 'Order ID unique',              'sales.orders',       'uniqueness',     'SELECT COUNT(*) = COUNT(DISTINCT order_id) FROM sales.orders',                                  'critical', 'sales',     'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp()),

-- ── Business logic checks ───────────────────────────────────────────────
('R010', 'Order amount positive',        'sales.orders',       'business_logic', 'SELECT COUNT(*) = 0 FROM sales.orders WHERE amount < 0',                                       'warning',  'sales',     'finance-analytics','daily', true, 'setup', current_timestamp(), current_timestamp()),
('R011', 'Valid order status',           'sales.orders',       'business_logic', 'SELECT COUNT(*) = 0 FROM sales.orders WHERE status NOT IN (''pending'',''processing'',''shipped'',''delivered'',''cancelled'')', 'warning', 'sales', 'finance-analytics', 'daily', true, 'setup', current_timestamp(), current_timestamp()),
('R012', 'Ship date after order date',   'sales.orders',       'business_logic', 'SELECT COUNT(*) = 0 FROM sales.orders WHERE ship_date < order_date',                            'warning',  'sales',     'finance-analytics','daily', true, 'setup', current_timestamp(), current_timestamp()),

-- ── Referential integrity checks ────────────────────────────────────────
('R013', 'Payments link to valid order', 'sales.payments',     'referential',    'SELECT COUNT(*) = 0 FROM sales.payments p LEFT JOIN sales.orders o ON p.order_id = o.order_id WHERE o.order_id IS NULL', 'warning', 'sales', 'finance-analytics', 'daily', true, 'setup', current_timestamp(), current_timestamp()),
('R014', 'Orders link to valid customer','sales.orders',       'referential',    'SELECT COUNT(*) = 0 FROM sales.orders o LEFT JOIN master.customers c ON o.customer_id = c.customer_id WHERE c.customer_id IS NULL', 'warning', 'sales', 'data-engineering', 'daily', true, 'setup', current_timestamp(), current_timestamp());


-- ── Seed a default alert config ─────────────────────────────────────────
INSERT INTO data_quality.alert_config (config_id, channel_type, channel_name, webhook_url, min_severity, is_default, active, created_at, updated_at)
VALUES
('TEAMS_DEFAULT', 'teams', 'Data Engineering Teams Channel', '{{REPLACE_WITH_TEAMS_WEBHOOK_URL}}', 'warning', true, true, current_timestamp(), current_timestamp());

INSERT INTO data_quality.alert_config (config_id, channel_type, channel_name, smtp_server, smtp_port, email_from, email_to, min_severity, is_default, active, created_at, updated_at)
VALUES
('EMAIL_DEFAULT', 'email', 'DQ Alert Email', 'smtp.office365.com', 587, '{{REPLACE_WITH_SENDER_EMAIL}}', '{{REPLACE_WITH_RECIPIENT_EMAILS}}', 'critical', true, true, current_timestamp(), current_timestamp());
