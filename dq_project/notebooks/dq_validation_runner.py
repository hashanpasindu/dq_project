# Databricks notebook source

# MAGIC %md
# MAGIC # 🛡️ DQ Validation Runner
# MAGIC Schedule this notebook as a Databricks Workflow.
# MAGIC It reads all active rules from `data_quality.rules`, executes each one,
# MAGIC writes results to `data_quality.results`, and sends alerts on failures.

# COMMAND ----------

# DBTITLE 1,Configuration
import requests
import json
import smtplib
import uuid
import time
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pyspark.sql import Row

RUN_ID = str(uuid.uuid4())[:8]
CHECKED_AT = datetime.now()

dbutils.widgets.text("dq_backend_catalog", "data_quality")
DQ_BACKEND_CATALOG = dbutils.widgets.get("dq_backend_catalog").strip() or "data_quality"
dbutils.widgets.dropdown("dq_env", "dev", ["dev", "test", "prod"])
DQ_ENV = dbutils.widgets.get("dq_env").strip().lower() or "dev"
dbutils.widgets.text("pipeline_tables", "")
PIPELINE_TABLES_RAW = dbutils.widgets.get("pipeline_tables").strip()


if DQ_ENV not in ("dev", "test", "prod"):
    raise ValueError("dq_env must be one of: dev, test, prod")


def env_schema(env_key: str) -> str:
    if env_key == "prod":
        return "prod_rules"
    if env_key == "test":
        return "test_rules"
    return "dev_rules"


DQ_SCHEMA = f"`{DQ_BACKEND_CATALOG}`.`{env_schema(DQ_ENV)}`"
RULES_TBL = f"{DQ_SCHEMA}.`rules`"
RESULTS_TBL = f"{DQ_SCHEMA}.`results`"
AUDIT_TBL = f"{DQ_SCHEMA}.`rule_audit`"
ALERT_CFG_TBL = f"{DQ_SCHEMA}.`alert_config`"
ALERT_LOG_TBL = f"{DQ_SCHEMA}.`alert_log`"

print(f"🚀 DQ Validation Run: {RUN_ID} at {CHECKED_AT}")
print(f"📦 DQ Backend Catalog: {DQ_BACKEND_CATALOG} | Env: {DQ_ENV}")


def normalize_table_name(name: str) -> str:
    return (name or "").replace("`", "").strip().lower()


def build_match_keys(name: str) -> set[str]:
    norm = normalize_table_name(name)
    if not norm:
        return set()
    parts = norm.split(".")
    keys = {norm}
    if len(parts) >= 2:
        keys.add(".".join(parts[-2:]))
    keys.add(parts[-1])
    return keys


pipeline_inputs = [
    p.strip()
    for p in re.split(r"[,;\n]", PIPELINE_TABLES_RAW)
    if p.strip()
]

pipeline_match_set = set()
for item in pipeline_inputs:
    pipeline_match_set.update(build_match_keys(item))

RUN_TYPE = "pipeline" if pipeline_match_set else "scheduled"
if pipeline_match_set:
    print(f"🎯 Pipeline table filter enabled ({len(pipeline_inputs)} inputs)")

# COMMAND ----------

# DBTITLE 1,Load active rules
rules_df = spark.sql(f"SELECT * FROM {RULES_TBL} WHERE active = true ORDER BY rule_id")
rules_all = rules_df.collect()

if pipeline_match_set:
    rules = [
        r for r in rules_all
        if bool(build_match_keys(r.dataset or "") & pipeline_match_set)
    ]
    print(f"📋 Loaded {len(rules_all)} active rules | {len(rules)} matched pipeline tables")
else:
    rules = rules_all
    print(f"📋 Loaded {len(rules)} active rules")

# COMMAND ----------

# DBTITLE 1,Execute all rules
results = []

for rule in rules:
    start = time.time()
    try:
        result_row = spark.sql(rule.rule_sql).collect()
        raw_value = str(result_row[0][0]) if result_row else "None"
        passed = bool(result_row[0][0]) if result_row else False
        error_message = None if passed else "Check returned FALSE"
    except Exception as e:
        passed = False
        raw_value = None
        error_message = str(e)[:500]

    elapsed = round(time.time() - start, 2)
    status = "✅" if passed else ("🔴" if rule.severity == "critical" else "🟡")
    print(f"  {status} {rule.rule_id} | {rule.dataset:<35} | {rule.rule_name:<40} | {elapsed}s")

    results.append(Row(
        run_id=RUN_ID,
        run_type=RUN_TYPE,
        rule_id=rule.rule_id,
        dataset=rule.dataset,
        rule_name=rule.rule_name,
        rule_type=rule.rule_type,
        severity=rule.severity,
        owner=rule.owner or "",
        passed=passed,
        error_message=error_message,
        result_value=raw_value,
        execution_time_sec=float(elapsed),
        checked_at=CHECKED_AT,
    ))

    # Update last_run status on the rule
    spark.sql(f"""
        UPDATE {RULES_TBL}
        SET last_run_at = current_timestamp(),
            last_run_passed = {str(passed).lower()}
        WHERE rule_id = '{rule.rule_id}'
    """)

# Write all results
if results:
    results_df = spark.createDataFrame(results)
    results_df.write.mode("append").saveAsTable(f"{DQ_BACKEND_CATALOG}.{env_schema(DQ_ENV)}.results")
else:
    print("ℹ️ No rules matched pipeline filter. Nothing to execute.")

print(f"\n{'='*80}")
failures = [r for r in results if not r.passed]
print(f"📊 {len(results) - len(failures)} passed | {len(failures)} failed")

# COMMAND ----------

# DBTITLE 1,Send alerts
def send_teams_alert(webhook_url, failures, run_id, total):
    """Send Teams Adaptive Card alert."""
    critical = [f for f in failures if f.severity == "critical"]
    warnings = [f for f in failures if f.severity == "warning"]

    rows_md = "\n".join([
        f"| {'🔴' if f.severity == 'critical' else '🟡'} | {f.rule_id} | {f.dataset} | {f.rule_name} | {(f.error_message or '')[:60]} |"
        for f in failures
    ])

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard", "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": "🛡️ Data Quality Alert", "weight": "Bolder", "size": "Large", "color": "Attention"},
                    {"type": "TextBlock", "text": f"Run {run_id} | {datetime.now().strftime('%Y-%m-%d %H:%M')}", "size": "Small", "isSubtle": True},
                    {"type": "ColumnSet", "columns": [
                        {"type": "Column", "items": [{"type": "TextBlock", "text": str(len(critical)), "size": "ExtraLarge", "color": "Attention", "horizontalAlignment": "Center"}, {"type": "TextBlock", "text": "Critical", "size": "Small", "horizontalAlignment": "Center"}]},
                        {"type": "Column", "items": [{"type": "TextBlock", "text": str(len(warnings)), "size": "ExtraLarge", "color": "Warning", "horizontalAlignment": "Center"}, {"type": "TextBlock", "text": "Warnings", "size": "Small", "horizontalAlignment": "Center"}]},
                        {"type": "Column", "items": [{"type": "TextBlock", "text": str(total - len(failures)), "size": "ExtraLarge", "color": "Good", "horizontalAlignment": "Center"}, {"type": "TextBlock", "text": "Passed", "size": "Small", "horizontalAlignment": "Center"}]},
                    ]},
                ]
            }
        }]
    }

    resp = requests.post(webhook_url, json=payload, timeout=30)
    return resp.status_code in (200, 202), resp.text


def send_email_alert(config, failures, run_id, total):
    """Send HTML email alert."""
    critical = [f for f in failures if f.severity == "critical"]
    warnings = [f for f in failures if f.severity == "warning"]

    rows_html = "".join([
        f"<tr><td>{'🔴' if f.severity == 'critical' else '🟡'}</td>"
        f"<td><b>{f.rule_id}</b></td><td>{f.dataset}</td>"
        f"<td>{f.rule_name}</td><td style='color:#666;font-size:12px;'>{(f.error_message or '')[:100]}</td></tr>"
        for f in failures
    ])

    html = f"""
    <h2>🛡️ DQ Alert — Run {run_id}</h2>
    <p><b>{len(critical)}</b> critical · <b>{len(warnings)}</b> warnings · <b>{total - len(failures)}</b> passed</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-family:Arial;font-size:13px;">
    <tr style="background:#f5f5f5;"><th></th><th>Rule</th><th>Table</th><th>Check</th><th>Detail</th></tr>
    {rows_html}
    </table>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🛡️ DQ Alert: {len(critical)} critical, {len(warnings)} warnings"
    msg["From"] = config.email_from
    recipients = [e.strip() for e in config.email_to.split(",")]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
        server.starttls()
        # Use secrets for password
        pwd = dbutils.secrets.get(scope="data-quality", key="smtp-password")
        server.login(config.email_from, pwd)
        server.sendmail(config.email_from, recipients, msg.as_string())
    return True, None


# ── Load alert configs and send ──
if failures:
    alert_configs = spark.sql(
        f"SELECT * FROM {ALERT_CFG_TBL} WHERE active = true AND is_default = true"
    ).collect()

    for ac in alert_configs:
        alert_id = uuid.uuid4().hex[:8]
        critical_n = len([f for f in failures if f.severity == "critical"])
        warning_n = len([f for f in failures if f.severity == "warning"])

        try:
            if ac.channel_type == "teams" and ac.webhook_url:
                ok, err = send_teams_alert(ac.webhook_url, failures, RUN_ID, len(results))
                status = "sent" if ok else "failed"
            elif ac.channel_type == "email" and ac.smtp_server:
                ok, err = send_email_alert(ac, failures, RUN_ID, len(results))
                status = "sent"
                err = None
            else:
                status = "skipped"
                err = f"Unsupported channel: {ac.channel_type}"
        except Exception as e:
            status = "failed"
            err = str(e)[:500]

        # Log the alert
        spark.sql(f"""
            INSERT INTO {ALERT_LOG_TBL} VALUES (
                '{alert_id}', '{RUN_ID}', '{ac.config_id}', '{ac.channel_type}',
                {len(failures)}, {critical_n}, {warning_n},
                '{len(failures)} DQ failures detected', '{status}',
                '{(err or "").replace("'", "")}', current_timestamp()
            )
        """)
        print(f"  📨 {ac.channel_type} ({ac.channel_name}): {status}")
else:
    print("✅ All checks passed — no alerts needed")

# COMMAND ----------

# DBTITLE 1,Fail job on critical issues (triggers Workflow retry/escalation)
critical_failures = [f for f in failures if f.severity == "critical"]
if critical_failures:
    msg = f"❌ {len(critical_failures)} CRITICAL DQ failures. Run: {RUN_ID}"
    print(msg)
    raise Exception(msg)
else:
    print(f"✅ Run {RUN_ID} complete. No critical failures.")
