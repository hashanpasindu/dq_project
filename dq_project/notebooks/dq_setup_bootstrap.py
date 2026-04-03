# Databricks notebook source

# MAGIC %md
# MAGIC # 🛡️ DQ Framework — One-Click Setup
# MAGIC Run all cells to create the backend tables, seed sample rules, and verify.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create schema
# MAGIC CREATE SCHEMA IF NOT EXISTS data_quality
# MAGIC COMMENT 'Data quality validation framework';

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 1. Rules table
# MAGIC CREATE TABLE IF NOT EXISTS data_quality.rules (
# MAGIC     rule_id STRING NOT NULL, rule_name STRING NOT NULL,
# MAGIC     dataset STRING NOT NULL, rule_type STRING NOT NULL,
# MAGIC     rule_sql STRING NOT NULL, severity STRING NOT NULL,
# MAGIC     category STRING, tags STRING, owner STRING,
# MAGIC     notification_channel STRING, schedule STRING DEFAULT 'daily',
# MAGIC     depends_on STRING, active BOOLEAN DEFAULT true,
# MAGIC     last_run_at TIMESTAMP, last_run_passed BOOLEAN,
# MAGIC     created_by STRING, created_at TIMESTAMP DEFAULT current_timestamp(),
# MAGIC     updated_by STRING, updated_at TIMESTAMP DEFAULT current_timestamp()
# MAGIC ) USING DELTA
# MAGIC TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 2. Results table
# MAGIC CREATE TABLE IF NOT EXISTS data_quality.results (
# MAGIC     run_id STRING NOT NULL, run_type STRING DEFAULT 'scheduled',
# MAGIC     rule_id STRING NOT NULL, dataset STRING NOT NULL,
# MAGIC     rule_name STRING NOT NULL, rule_type STRING NOT NULL,
# MAGIC     severity STRING NOT NULL, owner STRING, passed BOOLEAN NOT NULL,
# MAGIC     error_message STRING, result_value STRING,
# MAGIC     execution_time_sec DOUBLE, checked_at TIMESTAMP NOT NULL
# MAGIC ) USING DELTA PARTITIONED BY (checked_at);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 3. Audit table
# MAGIC CREATE TABLE IF NOT EXISTS data_quality.rule_audit (
# MAGIC     audit_id STRING NOT NULL, rule_id STRING NOT NULL,
# MAGIC     action STRING NOT NULL, field_changed STRING,
# MAGIC     old_value STRING, new_value STRING,
# MAGIC     changed_by STRING, changed_at TIMESTAMP DEFAULT current_timestamp()
# MAGIC ) USING DELTA;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 4. Alert config table
# MAGIC CREATE TABLE IF NOT EXISTS data_quality.alert_config (
# MAGIC     config_id STRING NOT NULL, channel_type STRING NOT NULL,
# MAGIC     channel_name STRING NOT NULL, webhook_url STRING,
# MAGIC     smtp_server STRING, smtp_port INT, email_from STRING, email_to STRING,
# MAGIC     min_severity STRING DEFAULT 'warning',
# MAGIC     is_default BOOLEAN DEFAULT false, active BOOLEAN DEFAULT true,
# MAGIC     created_at TIMESTAMP DEFAULT current_timestamp(),
# MAGIC     updated_at TIMESTAMP DEFAULT current_timestamp()
# MAGIC ) USING DELTA;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 5. Alert log table
# MAGIC CREATE TABLE IF NOT EXISTS data_quality.alert_log (
# MAGIC     alert_id STRING NOT NULL, run_id STRING NOT NULL,
# MAGIC     config_id STRING NOT NULL, channel_type STRING NOT NULL,
# MAGIC     num_failures INT NOT NULL, num_critical INT, num_warnings INT,
# MAGIC     message_summary STRING, status STRING NOT NULL,
# MAGIC     error_message STRING, sent_at TIMESTAMP DEFAULT current_timestamp()
# MAGIC ) USING DELTA;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Views
# MAGIC CREATE OR REPLACE VIEW data_quality.v_latest_run AS
# MAGIC SELECT * FROM data_quality.results
# MAGIC WHERE run_id = (SELECT run_id FROM data_quality.results ORDER BY checked_at DESC LIMIT 1);
# MAGIC
# MAGIC CREATE OR REPLACE VIEW data_quality.v_daily_summary AS
# MAGIC SELECT DATE(checked_at) AS check_date, COUNT(*) AS total_checks,
# MAGIC        SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS passed,
# MAGIC        SUM(CASE WHEN NOT passed THEN 1 ELSE 0 END) AS failed,
# MAGIC        SUM(CASE WHEN NOT passed AND severity='critical' THEN 1 ELSE 0 END) AS critical_fails,
# MAGIC        ROUND(SUM(CASE WHEN passed THEN 1 ELSE 0 END)*100.0/COUNT(*),1) AS pass_rate_pct
# MAGIC FROM data_quality.results WHERE checked_at >= current_date() - INTERVAL 90 DAYS
# MAGIC GROUP BY DATE(checked_at);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Verify
# MAGIC SHOW TABLES IN data_quality;

# COMMAND ----------

print("✅ All backend tables created successfully!")
print()
print("Tables:")
print("  data_quality.rules        — Rule definitions")
print("  data_quality.results      — Validation run history")
print("  data_quality.rule_audit   — Change tracking")
print("  data_quality.alert_config — Alert channels")
print("  data_quality.alert_log    — Alert delivery log")
print()
print("Views:")
print("  data_quality.v_latest_run     — Most recent run results")
print("  data_quality.v_daily_summary  — 90-day daily stats")
print()
print("Next steps:")
print("  1. Launch the Streamlit app:  cd streamlit_app && streamlit run app.py")
print("  2. Schedule notebooks/dq_validation_runner.py as a Workflow")
