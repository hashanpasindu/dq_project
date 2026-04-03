"""
db.py — Databricks connection & query layer
All database interactions go through this module.
"""

import time
import uuid
import re
import streamlit as st
from databricks import sql as dbx_sql


# ─── Connection ──────────────────────────────────────────────────────────────

def get_connection():
    """Create a fresh connection from session state config."""
    cfg = st.session_state["dbx_config"]
    return dbx_sql.connect(
        server_hostname=cfg["hostname"],
        http_path=cfg["http_path"],
        access_token=cfg["token"],
    )


def run_sql(sql: str, fetch: bool = True):
    """Execute SQL. Returns (column_names, rows) or (None, None)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        if fetch and cur.description:
            return [d[0] for d in cur.description], cur.fetchall()
        return None, None
    finally:
        conn.close()


def test_connection(hostname, http_path, token) -> tuple[bool, str]:
    """Test connectivity. Returns (success, message)."""
    try:
        conn = dbx_sql.connect(
            server_hostname=hostname, http_path=http_path, access_token=token
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
        return True, "Connected successfully"
    except Exception as e:
        return False, str(e)


def get_dq_catalog() -> str:
    """Return configured catalog for DQ backend tables."""
    cfg = st.session_state.get("dbx_config", {})
    return cfg.get("dq_catalog", "hive_metastore")


def dq_schema_fqn(catalog: str | None = None) -> str:
    """Return fully qualified data_quality schema path."""
    cat = catalog or get_dq_catalog()
    return f"`{cat}`.`data_quality`"


def detect_env(catalog: str | None = None) -> str:
    """Detect environment key from catalog name."""
    cat = (catalog or get_dq_catalog() or "").lower()
    if "prod" in cat:
        return "prod"
    if "uat" in cat or "test" in cat:
        return "uat"
    if "dev" in cat:
        return "dev"
    return "dev"


def dq_table_name(base_name: str, catalog: str | None = None) -> str:
    """Return environment-scoped table name (e.g. rules_dev)."""
    return f"{base_name}_{detect_env(catalog)}"


def dq_table(name: str, catalog: str | None = None) -> str:
    """Return fully qualified DQ table path."""
    return f"{dq_schema_fqn(catalog)}.`{dq_table_name(name, catalog)}`"


def ensure_results_table_shape(catalog: str | None = None) -> tuple[bool, list[str]]:
    """Ensure the results table has all required columns."""
    results_tbl = dq_table("results", catalog)
    required_cols = {
        "run_id": "STRING",
        "run_type": "STRING",
        "rule_id": "STRING",
        "dataset": "STRING",
        "rule_name": "STRING",
        "rule_type": "STRING",
        "severity": "STRING",
        "owner": "STRING",
        "passed": "BOOLEAN",
        "error_message": "STRING",
        "result_value": "STRING",
        "execution_time_sec": "DOUBLE",
        "checked_at": "TIMESTAMP",
    }
    failures = []
    try:
        _, rows = run_sql(f"SHOW COLUMNS IN {results_tbl}")
        existing = {str(r[0]).lower() for r in (rows or []) if r and r[0]}
        for col_name, col_type in required_cols.items():
            if col_name.lower() not in existing:
                try:
                    run_sql(
                        f"ALTER TABLE {results_tbl} ADD COLUMNS (`{col_name}` {col_type})",
                        fetch=False,
                    )
                except Exception as e:
                    failures.append(f"ALTER TABLE {results_tbl} ADD COLUMN {col_name} -> {e}")
    except Exception as e:
        failures.append(f"SHOW COLUMNS IN {results_tbl} -> {e}")
    return len(failures) == 0, failures


# ─── Discovery ───────────────────────────────────────────────────────────────

def discover_catalogs(hostname, http_path, token) -> list[str]:
    """Return all accessible Unity Catalog catalogs."""
    conn = dbx_sql.connect(
        server_hostname=hostname, http_path=http_path, access_token=token
    )
    try:
        cur = conn.cursor()
        cur.execute("SHOW CATALOGS")
        return sorted([
            r[0] for r in cur.fetchall()
            if r[0] not in ("system", "__databricks_internal")
        ])
    finally:
        conn.close()


def discover_schemas(hostname, http_path, token, catalog: str) -> list[str]:
    """Return all accessible schemas in a catalog."""
    conn = dbx_sql.connect(
        server_hostname=hostname, http_path=http_path, access_token=token
    )
    try:
        cur = conn.cursor()
        cur.execute(f"SHOW SCHEMAS IN `{catalog}`")
        return sorted([
            r[0] for r in cur.fetchall()
            if r[0] != "information_schema"
        ])
    finally:
        conn.close()


def discover_tables_in_schema(hostname, http_path, token, catalog: str, schema: str) -> list[str]:
    """Return all tables in a single catalog.schema as fully qualified names."""
    conn = dbx_sql.connect(
        server_hostname=hostname, http_path=http_path, access_token=token
    )
    try:
        cur = conn.cursor()
        cur.execute(f"SHOW TABLES IN `{catalog}`.`{schema}`")
        tables = []
        for row in cur.fetchall():
            table_name = row[1] if len(row) > 1 else row[0]
            tables.append(f"{catalog}.{schema}.{table_name}")
        return sorted(tables)
    finally:
        conn.close()


def discover_tables(hostname, http_path, token) -> list[str]:
    """Walk Unity Catalog and return all accessible tables."""
    conn = dbx_sql.connect(
        server_hostname=hostname, http_path=http_path, access_token=token
    )
    tables = []
    try:
        cur = conn.cursor()
        cur.execute("SHOW CATALOGS")
        cats = [
            r[0] for r in cur.fetchall()
            if r[0] not in ("system", "__databricks_internal")
        ]
        for cat in cats:
            try:
                cur.execute(f"SHOW SCHEMAS IN `{cat}`")
                schemas = [
                    r[0] for r in cur.fetchall()
                    if r[0] not in ("information_schema", "default")
                ]
                for sch in schemas:
                    try:
                        cur.execute(f"SHOW TABLES IN `{cat}`.`{sch}`")
                        for row in cur.fetchall():
                            tables.append(f"{cat}.{sch}.{row[1]}")
                    except Exception:
                        pass
            except Exception:
                pass
    finally:
        conn.close()
    return sorted(tables)


def discover_columns(table_name: str) -> list[dict]:
    """Return list of {name, type} for a table."""
    _, rows = run_sql(f"DESCRIBE TABLE {table_name}")
    if not rows:
        return []
    return [
        {"name": r[0], "type": r[1]}
        for r in rows
        if r[0] and not r[0].startswith("#") and not r[0].startswith(" ")
    ]


def get_table_sample(table_name: str, limit: int = 2) -> tuple[list[str], list[tuple], str | None]:
    """Return sample rows from a table for UI preview."""
    n = max(1, int(limit))
    try:
        cols, rows = run_sql(f"SELECT * FROM {table_name} LIMIT {n}")
        return cols or [], rows or [], None
    except Exception as e:
        return [], [], str(e)


# ─── Schema Bootstrap ────────────────────────────────────────────────────────

def ensure_dq_schema(catalog: str | None = None) -> tuple[bool, list[str]]:
    """Create all backend tables if they don't exist."""
    schema = dq_schema_fqn(catalog)
    rules_tbl = dq_table_name("rules", catalog)
    results_tbl = dq_table_name("results", catalog)
    audit_tbl = dq_table_name("rule_audit", catalog)
    alert_cfg_tbl = dq_table_name("alert_config", catalog)
    alert_log_tbl = dq_table_name("alert_log", catalog)
    ddls = [
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`{rules_tbl}` (
            rule_id STRING NOT NULL, rule_name STRING NOT NULL,
            dataset STRING NOT NULL, rule_type STRING NOT NULL,
            rule_sql STRING NOT NULL, severity STRING NOT NULL,
            category STRING, tags STRING, owner STRING,
            notification_channel STRING, schedule STRING DEFAULT 'daily',
            depends_on STRING, active BOOLEAN DEFAULT true,
            last_run_at TIMESTAMP, last_run_passed BOOLEAN,
            created_by STRING, created_at TIMESTAMP DEFAULT current_timestamp(),
            updated_by STRING, updated_at TIMESTAMP DEFAULT current_timestamp()
        ) USING DELTA TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`{results_tbl}` (
            run_id STRING NOT NULL, run_type STRING DEFAULT 'scheduled',
            rule_id STRING NOT NULL, dataset STRING NOT NULL,
            rule_name STRING NOT NULL, rule_type STRING NOT NULL,
            severity STRING NOT NULL, owner STRING, passed BOOLEAN NOT NULL,
            error_message STRING, result_value STRING,
            execution_time_sec DOUBLE, checked_at TIMESTAMP NOT NULL
        ) USING DELTA PARTITIONED BY (checked_at) TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`{audit_tbl}` (
            audit_id STRING NOT NULL, rule_id STRING NOT NULL,
            action STRING NOT NULL, field_changed STRING,
            old_value STRING, new_value STRING,
            changed_by STRING, changed_at TIMESTAMP DEFAULT current_timestamp()
        ) USING DELTA TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`{alert_cfg_tbl}` (
            config_id STRING NOT NULL, channel_type STRING NOT NULL,
            channel_name STRING NOT NULL, webhook_url STRING,
            smtp_server STRING, smtp_port INT, email_from STRING, email_to STRING,
            min_severity STRING DEFAULT 'warning',
            is_default BOOLEAN DEFAULT false, active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT current_timestamp(),
            updated_at TIMESTAMP DEFAULT current_timestamp()
        ) USING DELTA TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`{alert_log_tbl}` (
            alert_id STRING NOT NULL, run_id STRING NOT NULL,
            config_id STRING NOT NULL, channel_type STRING NOT NULL,
            num_failures INT NOT NULL, num_critical INT, num_warnings INT,
            message_summary STRING, status STRING NOT NULL,
            error_message STRING, sent_at TIMESTAMP DEFAULT current_timestamp()
        ) USING DELTA TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
    ]
    failures = []
    for ddl in ddls:
        try:
            run_sql(ddl, fetch=False)
        except Exception as e:
            head = ddl.strip().splitlines()[0]
            failures.append(f"{head} -> {e}")

    shape_ok, shape_failures = ensure_results_table_shape(catalog)
    if not shape_ok:
        failures.extend(shape_failures)

    return len(failures) == 0, failures


# ─── Rule CRUD ───────────────────────────────────────────────────────────────

def push_rule(rule: dict) -> tuple[bool, str | None]:
    """MERGE a rule into data_quality.rules. Returns (success, error)."""
    esc = lambda s: (s or "").replace("\\", "\\\\").replace("'", "\\'")
    rules_tbl = dq_table("rules")
    sql = f"""
        MERGE INTO {rules_tbl} AS t
        USING (SELECT '{rule["rule_id"]}' AS rule_id) AS s
        ON t.rule_id = s.rule_id
        WHEN MATCHED THEN UPDATE SET
            rule_name = '{esc(rule["rule_name"])}',
            dataset   = '{esc(rule["dataset"])}',
            rule_type = '{esc(rule["rule_type"])}',
            rule_sql  = '{esc(rule["rule_sql"])}',
            severity  = '{rule["severity"]}',
            category  = '{esc(rule.get("category", ""))}',
            owner     = '{esc(rule.get("owner", ""))}',
            active    = true,
            updated_by = '{esc(rule.get("updated_by", "streamlit"))}',
            updated_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (
            rule_id, rule_name, dataset, rule_type, rule_sql, severity,
            category, owner, active, created_by, created_at, updated_at
        ) VALUES (
            '{rule["rule_id"]}', '{esc(rule["rule_name"])}', '{esc(rule["dataset"])}',
            '{esc(rule["rule_type"])}', '{esc(rule["rule_sql"])}', '{rule["severity"]}',
            '{esc(rule.get("category", ""))}', '{esc(rule.get("owner", ""))}', true,
            '{esc(rule.get("created_by", "streamlit"))}', current_timestamp(), current_timestamp()
        )
    """
    try:
        run_sql(sql, fetch=False)
        # Log to audit
        log_audit(rule["rule_id"], "CREATED", changed_by=rule.get("created_by", "streamlit"))
        return True, None
    except Exception as e:
        return False, str(e)


def toggle_rule(rule_id: str, active: bool):
    """Enable or disable a rule."""
    rules_tbl = dq_table("rules")
    run_sql(
        f"UPDATE {rules_tbl} SET active={str(active).lower()}, "
        f"updated_at=current_timestamp() WHERE rule_id='{rule_id}'",
        fetch=False,
    )
    action = "ENABLED" if active else "DISABLED"
    log_audit(rule_id, action)


def delete_rule(rule_id: str):
    """Delete a rule permanently."""
    rules_tbl = dq_table("rules")
    run_sql(f"DELETE FROM {rules_tbl} WHERE rule_id='{rule_id}'", fetch=False)
    log_audit(rule_id, "DELETED")


def get_all_rules():
    """Fetch all rules ordered by rule_id."""
    rules_tbl = dq_table("rules")
    return run_sql("""
        SELECT rule_id, dataset, rule_name, rule_type, rule_sql,
               severity, category, owner, active, last_run_at, last_run_passed
        FROM {rules_tbl} ORDER BY rule_id
    """.format(rules_tbl=rules_tbl))


def get_active_rules():
    """Fetch only active rules."""
    rules_tbl = dq_table("rules")
    return run_sql("""
        SELECT rule_id, dataset, rule_name, rule_type, rule_sql,
               severity, owner, active
        FROM {rules_tbl} WHERE active = true ORDER BY rule_id
    """.format(rules_tbl=rules_tbl))


# ─── Testing ─────────────────────────────────────────────────────────────────

def test_rule(sql: str) -> tuple[bool, float, str | None]:
    """Execute a rule SQL. Returns (passed, elapsed_sec, error)."""
    start = time.time()
    try:
        _, rows = run_sql(sql)
        elapsed = round(time.time() - start, 2)
        passed = bool(rows[0][0]) if rows else False
        return passed, elapsed, None
    except Exception as e:
        return False, round(time.time() - start, 2), str(e)


# ─── Audit ───────────────────────────────────────────────────────────────────

def log_audit(rule_id: str, action: str, field_changed: str = None,
              old_value: str = None, new_value: str = None,
              changed_by: str = "streamlit"):
    """Write an entry to the rule_audit table."""
    aid = uuid.uuid4().hex[:8]
    esc = lambda s: (s or "").replace("'", "\\'") if s else ""
    audit_tbl = dq_table("rule_audit")
    try:
        run_sql(f"""
            INSERT INTO {audit_tbl}
            VALUES ('{aid}', '{rule_id}', '{action}',
                    '{esc(field_changed)}', '{esc(old_value)}',
                    '{esc(new_value)}', '{esc(changed_by)}', current_timestamp())
        """, fetch=False)
    except Exception:
        pass  # Don't fail the main operation if audit logging fails


# ─── Results ─────────────────────────────────────────────────────────────────

def get_latest_run():
    """Get all results from the most recent validation run."""
    results_tbl = dq_table("results")
    return run_sql("""
        SELECT rule_id, dataset, rule_name, severity, passed,
               error_message, execution_time_sec, checked_at, run_id
        FROM {results_tbl}
        WHERE run_id = (SELECT run_id FROM {results_tbl} ORDER BY checked_at DESC LIMIT 1)
        ORDER BY passed ASC, severity DESC
    """.format(results_tbl=results_tbl))


def get_daily_trend():
    """Get 30-day daily failure trend."""
    results_tbl = dq_table("results")
    return run_sql("""
        SELECT DATE(checked_at) AS dt,
               SUM(CASE WHEN NOT passed AND severity='critical' THEN 1 ELSE 0 END) AS critical,
               SUM(CASE WHEN NOT passed AND severity='warning' THEN 1 ELSE 0 END)  AS warnings,
               SUM(CASE WHEN passed THEN 1 ELSE 0 END)                             AS passed
        FROM {results_tbl}
        WHERE checked_at >= current_date() - INTERVAL 30 DAYS
        GROUP BY DATE(checked_at) ORDER BY dt
    """.format(results_tbl=results_tbl))


def get_audit_log(limit: int = 50):
    """Get recent audit entries."""
    audit_tbl = dq_table("rule_audit")
    return run_sql(f"""
        SELECT audit_id, rule_id, action, field_changed,
               old_value, new_value, changed_by, changed_at
        FROM {audit_tbl}
        ORDER BY changed_at DESC LIMIT {limit}
    """)


def _replace_catalog_refs(text: str, old_catalog: str, new_catalog: str) -> str:
    """Replace catalog qualifiers in SQL/table strings."""
    if not text:
        return text

    old_esc = re.escape(old_catalog)
    out = re.sub(rf"`{old_esc}`\\.", f"`{new_catalog}`.", text, flags=re.IGNORECASE)
    out = re.sub(rf"(?<![A-Za-z0-9_]){old_esc}\\.", f"{new_catalog}.", out, flags=re.IGNORECASE)
    return out


def remap_rule_catalog_references(
    old_catalog: str,
    new_catalog: str,
    rule_ids: list[str] | None = None,
) -> tuple[int, int, list[str]]:
    """Update stored rules to point from one data catalog to another."""
    old_catalog = (old_catalog or "").strip()
    new_catalog = (new_catalog or "").strip()
    if not old_catalog or not new_catalog:
        return 0, 0, ["Both source and target catalogs are required"]
    if old_catalog == new_catalog:
        return 0, 0, ["Source and target catalogs are the same"]
    if rule_ids is not None and not rule_ids:
        return 0, 0, ["Select at least one rule to promote"]

    rules_tbl = dq_table("rules")
    query = f"SELECT rule_id, dataset, rule_sql FROM {rules_tbl}"
    if rule_ids is not None:
        esc = lambda s: (s or "").replace("\\", "\\\\").replace("'", "\\'")
        rule_id_list = ", ".join(f"'{esc(rule_id)}'" for rule_id in rule_ids)
        query += f" WHERE rule_id IN ({rule_id_list})"
    _, rows = run_sql(query)
    rows = rows or []

    checked = len(rows)
    updated = 0
    errors = []

    for rid, dataset, rule_sql in rows:
        new_dataset = _replace_catalog_refs(dataset or "", old_catalog, new_catalog)
        new_rule_sql = _replace_catalog_refs(rule_sql or "", old_catalog, new_catalog)

        if new_dataset == (dataset or "") and new_rule_sql == (rule_sql or ""):
            continue

        esc = lambda s: (s or "").replace("\\", "\\\\").replace("'", "\\'")
        try:
            run_sql(
                f"""
                UPDATE {rules_tbl}
                SET dataset='{esc(new_dataset)}',
                    rule_sql='{esc(new_rule_sql)}',
                    updated_at=current_timestamp()
                WHERE rule_id='{esc(rid)}'
                """,
                fetch=False,
            )
            log_audit(
                rid,
                "CATALOG_REMAP",
                field_changed="dataset,rule_sql",
                old_value=f"{old_catalog}",
                new_value=f"{new_catalog}",
            )
            updated += 1
        except Exception as e:
            errors.append(f"{rid}: {e}")

    return checked, updated, errors
