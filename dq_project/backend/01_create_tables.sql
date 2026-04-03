-- ════════════════════════════════════════════════════════════════════════════
-- 🛡️ DATA QUALITY FRAMEWORK — BACKEND TABLE STRUCTURE
-- ════════════════════════════════════════════════════════════════════════════
-- Run this ONCE in Databricks SQL Editor or a notebook (%sql magic).
-- All tables are Delta format under the `data_quality` schema.
-- ════════════════════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────────────────────────────────
-- 0. CREATE SCHEMA
-- ─────────────────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS data_quality
COMMENT 'Data quality validation framework — rules, results, alerts, and audit trail';


-- ─────────────────────────────────────────────────────────────────────────
-- 1. RULES TABLE — Central rule configuration
-- ─────────────────────────────────────────────────────────────────────────
-- This is the master table that the Streamlit app writes to and the
-- validation runner reads from. Each row is one DQ check.

CREATE TABLE IF NOT EXISTS data_quality.rules (

    -- Identity
    rule_id         STRING          NOT NULL    COMMENT 'Unique rule identifier (e.g. R001, R_A1B2C3)',
    rule_name       STRING          NOT NULL    COMMENT 'Human-readable rule name',

    -- What to check
    dataset         STRING          NOT NULL    COMMENT 'Fully qualified table name (catalog.schema.table)',
    rule_type       STRING          NOT NULL    COMMENT 'Category: freshness | row_count | null_check | uniqueness | business_logic | referential | custom',
    rule_sql        STRING          NOT NULL    COMMENT 'SQL that returns TRUE (pass) or FALSE (fail). Must be a single SELECT.',

    -- Classification
    severity        STRING          NOT NULL    COMMENT 'Alert level: critical | warning | info',
    category        STRING                      COMMENT 'Business domain: finance, sales, marketing, operations, etc.',
    tags            STRING                      COMMENT 'Comma-separated labels for filtering (e.g. daily,tier1,pii)',

    -- Ownership
    owner           STRING                      COMMENT 'Team or person responsible (e.g. data-engineering, john.doe)',
    notification_channel STRING                 COMMENT 'Override alert channel: teams | email | slack | pagerduty',

    -- Scheduling
    schedule        STRING          DEFAULT 'daily'   COMMENT 'When to run: daily | hourly | on_pipeline | manual',
    depends_on      STRING                      COMMENT 'Comma-separated rule_ids that must pass first',

    -- State
    active          BOOLEAN         DEFAULT true COMMENT 'Toggle rule on/off without deleting',
    last_run_at     TIMESTAMP                   COMMENT 'Timestamp of last execution',
    last_run_passed BOOLEAN                     COMMENT 'Result of last execution',

    -- Audit
    created_by      STRING                      COMMENT 'Who created this rule',
    created_at      TIMESTAMP       DEFAULT current_timestamp(),
    updated_by      STRING                      COMMENT 'Who last modified this rule',
    updated_at      TIMESTAMP       DEFAULT current_timestamp(),

    -- Constraints
    CONSTRAINT rules_pk PRIMARY KEY (rule_id),
    CONSTRAINT valid_severity CHECK (severity IN ('critical', 'warning', 'info')),
    CONSTRAINT valid_rule_type CHECK (rule_type IN ('freshness', 'row_count', 'null_check', 'uniqueness', 'business_logic', 'referential', 'custom')),
    CONSTRAINT valid_schedule CHECK (schedule IN ('daily', 'hourly', 'on_pipeline', 'manual'))
)
USING DELTA
COMMENT 'DQ rule definitions — each row is one validation check'
TBLPROPERTIES (
    'delta.enableChangeDataFeed' = 'true',          -- track changes for audit
    'delta.autoOptimize.optimizeWrite' = 'true'
);


-- ─────────────────────────────────────────────────────────────────────────
-- 2. RESULTS TABLE — Validation run history (append-only log)
-- ─────────────────────────────────────────────────────────────────────────
-- Every time the validation runner executes, it appends one row per rule.
-- This is the data source for dashboards and trend analysis.

CREATE TABLE IF NOT EXISTS data_quality.results (

    -- Run identity
    run_id          STRING          NOT NULL    COMMENT 'Unique run identifier (UUID short hash)',
    run_type        STRING          DEFAULT 'scheduled' COMMENT 'How was this run triggered: scheduled | manual | on_pipeline',

    -- Rule reference
    rule_id         STRING          NOT NULL    COMMENT 'FK to data_quality.rules.rule_id',
    dataset         STRING          NOT NULL    COMMENT 'Table that was checked (denormalized for query speed)',
    rule_name       STRING          NOT NULL    COMMENT 'Rule name (denormalized)',
    rule_type       STRING          NOT NULL    COMMENT 'Rule type (denormalized)',
    severity        STRING          NOT NULL    COMMENT 'Severity at time of check (denormalized)',
    owner           STRING                      COMMENT 'Owner at time of check (denormalized)',

    -- Outcome
    passed          BOOLEAN         NOT NULL    COMMENT 'TRUE = check passed, FALSE = failed',
    error_message   STRING                      COMMENT 'Error details if failed or errored during execution',
    result_value    STRING                      COMMENT 'Raw return value from the SQL for debugging',

    -- Performance
    execution_time_sec DOUBLE                   COMMENT 'Seconds taken to evaluate this rule',

    -- Timestamp
    checked_at      TIMESTAMP       NOT NULL    COMMENT 'When this check was executed'
)
USING DELTA
COMMENT 'Append-only log of every DQ rule execution'
PARTITIONED BY (checked_at)
TBLPROPERTIES (
    'delta.autoOptimize.optimizeWrite' = 'true',
    'delta.autoOptimize.autoCompact'   = 'true'
);


-- ─────────────────────────────────────────────────────────────────────────
-- 3. RULE AUDIT TABLE — Change history for rules
-- ─────────────────────────────────────────────────────────────────────────
-- Tracks every INSERT, UPDATE, DELETE on the rules table.
-- Populated by the Streamlit app and/or a Delta CDF consumer.

CREATE TABLE IF NOT EXISTS data_quality.rule_audit (

    audit_id        STRING          NOT NULL    COMMENT 'Unique audit entry ID',
    rule_id         STRING          NOT NULL    COMMENT 'Which rule was changed',
    action          STRING          NOT NULL    COMMENT 'What happened: CREATED | UPDATED | DELETED | ENABLED | DISABLED',

    -- Snapshot of what changed
    field_changed   STRING                      COMMENT 'Which field changed (for UPDATED actions)',
    old_value       STRING                      COMMENT 'Previous value',
    new_value       STRING                      COMMENT 'New value',

    -- Who & when
    changed_by      STRING                      COMMENT 'User who made the change',
    changed_at      TIMESTAMP       DEFAULT current_timestamp()
)
USING DELTA
COMMENT 'Audit trail for all rule changes'
TBLPROPERTIES (
    'delta.autoOptimize.optimizeWrite' = 'true'
);


-- ─────────────────────────────────────────────────────────────────────────
-- 4. ALERT CONFIG TABLE — Where to send notifications
-- ─────────────────────────────────────────────────────────────────────────
-- Central place to manage alert channels. The validation runner reads this
-- to decide where to send failure notifications.

CREATE TABLE IF NOT EXISTS data_quality.alert_config (

    config_id       STRING          NOT NULL    COMMENT 'Unique config identifier',
    channel_type    STRING          NOT NULL    COMMENT 'teams | email | slack | pagerduty',
    channel_name    STRING          NOT NULL    COMMENT 'Human-readable name (e.g. Data Eng Teams Channel)',

    -- Connection details (use Databricks Secrets for sensitive values)
    webhook_url     STRING                      COMMENT 'Webhook URL for Teams/Slack (store in secrets for prod)',
    smtp_server     STRING                      COMMENT 'SMTP server for email',
    smtp_port       INT                         COMMENT 'SMTP port (587 for TLS)',
    email_from      STRING                      COMMENT 'Sender email address',
    email_to        STRING                      COMMENT 'Comma-separated recipient emails',

    -- Behavior
    min_severity    STRING          DEFAULT 'warning' COMMENT 'Minimum severity to trigger: critical | warning | info',
    is_default      BOOLEAN         DEFAULT false     COMMENT 'Use this channel when rule has no override',
    active          BOOLEAN         DEFAULT true      COMMENT 'Enable/disable this channel',

    -- Audit
    created_at      TIMESTAMP       DEFAULT current_timestamp(),
    updated_at      TIMESTAMP       DEFAULT current_timestamp(),

    CONSTRAINT alert_config_pk PRIMARY KEY (config_id),
    CONSTRAINT valid_channel CHECK (channel_type IN ('teams', 'email', 'slack', 'pagerduty'))
)
USING DELTA
COMMENT 'Alert channel configuration for DQ notifications';


-- ─────────────────────────────────────────────────────────────────────────
-- 5. ALERT LOG TABLE — Record of every alert sent
-- ─────────────────────────────────────────────────────────────────────────
-- Every alert the system sends is logged here for debugging and audit.

CREATE TABLE IF NOT EXISTS data_quality.alert_log (

    alert_id        STRING          NOT NULL    COMMENT 'Unique alert identifier',
    run_id          STRING          NOT NULL    COMMENT 'Which validation run triggered this alert',
    config_id       STRING          NOT NULL    COMMENT 'FK to alert_config — which channel was used',
    channel_type    STRING          NOT NULL    COMMENT 'teams | email | slack | pagerduty',

    -- Content
    num_failures    INT             NOT NULL    COMMENT 'How many rules failed in this alert',
    num_critical    INT                         COMMENT 'Critical failures in this alert',
    num_warnings    INT                         COMMENT 'Warning failures in this alert',
    message_summary STRING                      COMMENT 'Short summary of the alert content',

    -- Delivery
    status          STRING          NOT NULL    COMMENT 'sent | failed | skipped',
    error_message   STRING                      COMMENT 'Error if delivery failed',
    sent_at         TIMESTAMP       DEFAULT current_timestamp()
)
USING DELTA
COMMENT 'Log of every DQ alert sent';


-- ─────────────────────────────────────────────────────────────────────────
-- 6. VIEWS — Pre-built views for dashboards
-- ─────────────────────────────────────────────────────────────────────────

-- Latest run results
CREATE OR REPLACE VIEW data_quality.v_latest_run AS
SELECT *
FROM data_quality.results
WHERE run_id = (
    SELECT run_id FROM data_quality.results ORDER BY checked_at DESC LIMIT 1
);

-- Daily failure summary (last 90 days)
CREATE OR REPLACE VIEW data_quality.v_daily_summary AS
SELECT
    DATE(checked_at)                                                         AS check_date,
    COUNT(*)                                                                 AS total_checks,
    SUM(CASE WHEN passed THEN 1 ELSE 0 END)                                AS passed,
    SUM(CASE WHEN NOT passed THEN 1 ELSE 0 END)                            AS failed,
    SUM(CASE WHEN NOT passed AND severity = 'critical' THEN 1 ELSE 0 END)  AS critical_fails,
    SUM(CASE WHEN NOT passed AND severity = 'warning' THEN 1 ELSE 0 END)   AS warning_fails,
    ROUND(SUM(CASE WHEN passed THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1)  AS pass_rate_pct,
    ROUND(SUM(execution_time_sec), 1)                                       AS total_runtime_sec
FROM data_quality.results
WHERE checked_at >= current_date() - INTERVAL 90 DAYS
GROUP BY DATE(checked_at);

-- Repeat offenders (most failing rules in last 30 days)
CREATE OR REPLACE VIEW data_quality.v_repeat_offenders AS
SELECT
    r.rule_id,
    r.dataset,
    r.rule_name,
    r.severity,
    r.owner,
    COUNT(*)                                                    AS total_runs,
    SUM(CASE WHEN NOT res.passed THEN 1 ELSE 0 END)           AS failure_count,
    ROUND(
        SUM(CASE WHEN NOT res.passed THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                           AS failure_pct,
    MAX(res.checked_at)                                         AS last_checked
FROM data_quality.rules r
JOIN data_quality.results res ON r.rule_id = res.rule_id
WHERE res.checked_at >= current_date() - INTERVAL 30 DAYS
GROUP BY r.rule_id, r.dataset, r.rule_name, r.severity, r.owner
HAVING failure_count > 0
ORDER BY failure_count DESC;

-- Active rules overview
CREATE OR REPLACE VIEW data_quality.v_active_rules AS
SELECT
    rule_id, dataset, rule_name, rule_type, severity,
    category, owner, schedule, last_run_at, last_run_passed
FROM data_quality.rules
WHERE active = true
ORDER BY severity DESC, dataset, rule_id;


-- ─────────────────────────────────────────────────────────────────────────
-- DONE
-- ─────────────────────────────────────────────────────────────────────────
-- Tables created:
--   data_quality.rules          → Rule definitions
--   data_quality.results        → Run history (append-only)
--   data_quality.rule_audit     → Change tracking
--   data_quality.alert_config   → Alert channel setup
--   data_quality.alert_log      → Alert delivery log
--
-- Views created:
--   data_quality.v_latest_run        → Last run results
--   data_quality.v_daily_summary     → 90-day daily stats
--   data_quality.v_repeat_offenders  → Most failing rules
--   data_quality.v_active_rules      → Active rules list
-- ─────────────────────────────────────────────────────────────────────────
