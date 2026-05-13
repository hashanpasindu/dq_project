"""
db.py — Databricks connection & query layer
All database interactions go through this module.
"""

import time
import uuid
import re
import streamlit as st
from databricks import sql as dbx_sql


# Connector defaults are generous (up to ~15 min retry windows). Keep these
# short so the UI fails fast with actionable errors when a warehouse is down.
DBX_SOCKET_TIMEOUT_SEC = 25
DBX_RETRY_ATTEMPTS = 3
DBX_RETRY_MAX_DURATION_SEC = 45
DQ_BACKEND_CATALOG = "data_quality"


def _normalize_hostname(hostname: str) -> str:
    val = (hostname or "").strip()
    if val.startswith("https://"):
        val = val[len("https://") :]
    elif val.startswith("http://"):
        val = val[len("http://") :]
    return val.rstrip("/")


def _normalize_http_path(http_path: str) -> str:
    val = (http_path or "").strip()
    if not val:
        return val
    if not val.startswith("/"):
        val = f"/{val}"
    return val


def _build_tls_kwargs(tls_ca_file: str | None = None) -> dict:
    kwargs = {}
    ca_file = (tls_ca_file or "").strip()
    if ca_file:
        kwargs["_tls_trusted_ca_file"] = ca_file
    return kwargs


def _dbx_connect(
    hostname: str,
    http_path: str,
    token: str,
    tls_ca_file: str | None = None,
):
    return dbx_sql.connect(
        server_hostname=_normalize_hostname(hostname),
        http_path=_normalize_http_path(http_path),
        access_token=(token or "").strip(),
        _socket_timeout=DBX_SOCKET_TIMEOUT_SEC,
        _retry_stop_after_attempts_count=DBX_RETRY_ATTEMPTS,
        _retry_stop_after_attempts_duration=DBX_RETRY_MAX_DURATION_SEC,
        _retry_delay_min=1,
        _retry_delay_max=5,
        **_build_tls_kwargs(tls_ca_file=tls_ca_file),
    )


# ─── Connection ──────────────────────────────────────────────────────────────

def get_connection():
    """Create a fresh connection from session state config."""
    cfg = st.session_state["dbx_config"]
    return _dbx_connect(
        cfg["hostname"],
        cfg["http_path"],
        cfg["token"],
        tls_ca_file=cfg.get("tls_ca_file", ""),
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


def test_connection(
    hostname,
    http_path,
    token,
    tls_ca_file: str | None = None,
) -> tuple[bool, str]:
    """Test connectivity. Returns (success, message)."""
    host = _normalize_hostname(hostname)
    path = _normalize_http_path(http_path)
    tok = (token or "").strip()

    if not all([host, path, tok]):
        return False, "Missing hostname, HTTP path, or token"

    try:
        conn = _dbx_connect(
            host,
            path,
            tok,
            tls_ca_file=tls_ca_file,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        conn.close()
        return True, "Connected successfully"
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "invalid access token" in msg.lower():
            return False, "Authentication failed. Check PAT token and SQL Warehouse permissions."
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            return False, "Connection timed out. Verify warehouse is running and network allows Databricks SQL endpoint."
        if "certificate_verify_failed" in msg.lower() or "self-signed certificate" in msg.lower():
            return False, "SSL certificate verification failed. Add your corporate/root CA PEM path in 'Trusted CA file path'."
        if "not found" in msg.lower() and "warehouse" in msg.lower():
            return False, "SQL Warehouse not found. Verify the HTTP path for this workspace."
        return False, msg


def get_dq_catalog() -> str:
    """Return configured data catalog used for env detection in UI."""
    cfg = st.session_state.get("dbx_config", {})
    return cfg.get("dq_catalog", "hive_metastore")


def env_schema_name(env_key: str) -> str:
    env = (env_key or "dev").lower()
    if env == "prod":
        return "prod_rules"
    if env in ("test", "uat"):
        return "test_rules"
    return "dev_rules"


def dq_schema_fqn(catalog: str | None = None) -> str:
    """Return fully qualified DQ schema path in fixed data_quality catalog."""
    env = detect_env(catalog)
    return f"`{DQ_BACKEND_CATALOG}`.`{env_schema_name(env)}`"


def detect_env(catalog: str | None = None) -> str:
    """Detect environment key from catalog name."""
    cat = (catalog or get_dq_catalog() or "").lower()
    if "prod" in cat:
        return "prod"
    if "uat" in cat or "test" in cat:
        return "test"
    if "dev" in cat:
        return "dev"
    return "dev"


def dq_table(name: str, catalog: str | None = None) -> str:
    """Return fully qualified DQ table path."""
    return f"{dq_schema_fqn(catalog)}.`{name}`"


def dq_table_for_env(name: str, env_key: str) -> str:
    """Return fully qualified DQ table path for an explicit environment."""
    return f"`{DQ_BACKEND_CATALOG}`.`{env_schema_name(env_key)}`.`{name}`"


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

def discover_catalogs(
    hostname,
    http_path,
    token,
    tls_ca_file: str | None = None,
) -> list[str]:
    """Return all accessible Unity Catalog catalogs."""
    conn = _dbx_connect(
        hostname,
        http_path,
        token,
        tls_ca_file=tls_ca_file,
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


def discover_schemas(
    hostname,
    http_path,
    token,
    catalog: str,
    tls_ca_file: str | None = None,
) -> list[str]:
    """Return all accessible schemas in a catalog."""
    conn = _dbx_connect(
        hostname,
        http_path,
        token,
        tls_ca_file=tls_ca_file,
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


def discover_tables_in_schema(
    hostname,
    http_path,
    token,
    catalog: str,
    schema: str,
    tls_ca_file: str | None = None,
) -> list[str]:
    """Return all tables in a single catalog.schema as fully qualified names."""
    conn = _dbx_connect(
        hostname,
        http_path,
        token,
        tls_ca_file=tls_ca_file,
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


def discover_tables(
    hostname,
    http_path,
    token,
    tls_ca_file: str | None = None,
) -> list[str]:
    """Walk Unity Catalog and return all accessible tables."""
    conn = _dbx_connect(
        hostname,
        http_path,
        token,
        tls_ca_file=tls_ca_file,
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
    """Create backend schemas/tables for dev, test, and prod rules."""
    try:
        _, cat_rows = run_sql("SHOW CATALOGS")
        catalogs = {str(r[0]).strip().lower() for r in (cat_rows or []) if r and r[0]}
    except Exception as e:
        return False, [f"SHOW CATALOGS -> {e}"]

    if DQ_BACKEND_CATALOG.lower() not in catalogs:
        return False, [
            (
                f"Catalog `{DQ_BACKEND_CATALOG}` does not exist. "
                "Create it in Databricks Catalog Explorer (UI) with Default Storage, "
                "or create it with an explicit MANAGED LOCATION, then retry."
            )
        ]

    ddls = []
    for env_key in ("dev", "test", "prod"):
        schema = f"`{DQ_BACKEND_CATALOG}`.`{env_schema_name(env_key)}`"
        ddls.extend([
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`rules` (
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
        f"""CREATE TABLE IF NOT EXISTS {schema}.`results` (
            run_id STRING NOT NULL, run_type STRING DEFAULT 'scheduled',
            rule_id STRING NOT NULL, dataset STRING NOT NULL,
            rule_name STRING NOT NULL, rule_type STRING NOT NULL,
            severity STRING NOT NULL, owner STRING, passed BOOLEAN NOT NULL,
            error_message STRING, result_value STRING,
            execution_time_sec DOUBLE, checked_at TIMESTAMP NOT NULL
        ) USING DELTA PARTITIONED BY (checked_at) TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`rule_audit` (
            audit_id STRING NOT NULL, rule_id STRING NOT NULL,
            action STRING NOT NULL, field_changed STRING,
            old_value STRING, new_value STRING,
            changed_by STRING, changed_at TIMESTAMP DEFAULT current_timestamp()
        ) USING DELTA TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`alert_config` (
            config_id STRING NOT NULL, channel_type STRING NOT NULL,
            channel_name STRING NOT NULL, webhook_url STRING,
            smtp_server STRING, smtp_port INT, email_from STRING, email_to STRING,
            min_severity STRING DEFAULT 'warning',
            is_default BOOLEAN DEFAULT false, active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT current_timestamp(),
            updated_at TIMESTAMP DEFAULT current_timestamp()
        ) USING DELTA TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.`alert_log` (
            alert_id STRING NOT NULL, run_id STRING NOT NULL,
            config_id STRING NOT NULL, channel_type STRING NOT NULL,
            num_failures INT NOT NULL, num_critical INT, num_warnings INT,
            message_summary STRING, status STRING NOT NULL,
            error_message STRING, sent_at TIMESTAMP DEFAULT current_timestamp()
        ) USING DELTA TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported')""",
        ])
    failures = []
    for ddl in ddls:
        try:
            run_sql(ddl, fetch=False)
        except Exception as e:
            head = ddl.strip().splitlines()[0]
            failures.append(f"{head} -> {e}")

    for env_key in ("dev", "test", "prod"):
        shape_ok, shape_failures = ensure_results_table_shape(env_key)
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
            tags      = '{esc(rule.get("tags", ""))}',
            owner     = '{esc(rule.get("owner", ""))}',
            active    = true,
            updated_by = '{esc(rule.get("updated_by", "streamlit"))}',
            updated_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (
            rule_id, rule_name, dataset, rule_type, rule_sql, severity,
            category, tags, owner, active, created_by, created_at, updated_at
        ) VALUES (
            '{rule["rule_id"]}', '{esc(rule["rule_name"])}', '{esc(rule["dataset"])}',
            '{esc(rule["rule_type"])}', '{esc(rule["rule_sql"])}', '{rule["severity"]}',
            '{esc(rule.get("category", ""))}', '{esc(rule.get("tags", ""))}',
            '{esc(rule.get("owner", ""))}', true,
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


def toggle_rule(rule_id: str, active: bool, catalog: str | None = None):
    """Enable or disable a rule."""
    rules_tbl = dq_table("rules", catalog)
    run_sql(
        f"UPDATE {rules_tbl} SET active={str(active).lower()}, "
        f"updated_at=current_timestamp() WHERE rule_id='{rule_id}'",
        fetch=False,
    )
    action = "ENABLED" if active else "DISABLED"
    log_audit(rule_id, action)


def delete_rule(rule_id: str, catalog: str | None = None):
    """Delete a rule permanently."""
    rules_tbl = dq_table("rules", catalog)
    run_sql(f"DELETE FROM {rules_tbl} WHERE rule_id='{rule_id}'", fetch=False)
    log_audit(rule_id, "DELETED")


def get_all_rules(catalog: str | None = None):
    """Fetch all rules ordered by rule_id."""
    rules_tbl = dq_table("rules", catalog)
    return run_sql("""
        SELECT rule_id, dataset, rule_name, rule_type, rule_sql,
               severity, category, owner, active, last_run_at, last_run_passed, tags
        FROM {rules_tbl} ORDER BY rule_id
    """.format(rules_tbl=rules_tbl))


def get_active_rules(catalog: str | None = None):
    """Fetch only active rules."""
    rules_tbl = dq_table("rules", catalog)
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
    out = re.sub(rf"`{old_esc}`\.", f"`{new_catalog}`.", text, flags=re.IGNORECASE)
    out = re.sub(rf"(?<![A-Za-z0-9_]){old_esc}\.", f"{new_catalog}.", out, flags=re.IGNORECASE)
    return out


def remap_rule_catalog_references(
    old_catalog: str,
    new_catalog: str,
    rule_ids: list[str] | None = None,
) -> tuple[int, int, list[str]]:
    """Promote selected rules from source env to target env and remap catalogs."""
    old_catalog = (old_catalog or "").strip()
    new_catalog = (new_catalog or "").strip()
    if not old_catalog or not new_catalog:
        return 0, 0, ["Both source and target catalogs are required"]
    if old_catalog == new_catalog:
        return 0, 0, ["Source and target catalogs are the same"]
    if rule_ids is not None and not rule_ids:
        return 0, 0, ["Select at least one rule to promote"]

    source_env = detect_env(old_catalog)
    target_env = detect_env(new_catalog)
    source_rules_tbl = dq_table_for_env("rules", source_env)
    target_rules_tbl = dq_table_for_env("rules", target_env)

    query = (
        "SELECT rule_id, rule_name, dataset, rule_type, rule_sql, severity, "
        "category, owner, active, created_by "
        f"FROM {source_rules_tbl}"
    )
    if rule_ids is not None:
        esc = lambda s: (s or "").replace("\\", "\\\\").replace("'", "\\'")
        rule_id_list = ", ".join(f"'{esc(rule_id)}'" for rule_id in rule_ids)
        query += f" WHERE rule_id IN ({rule_id_list})"
    _, rows = run_sql(query)
    rows = rows or []

    checked = len(rows)
    updated = 0
    errors = []

    for rid, rule_name, dataset, rule_type, rule_sql, severity, category, owner, active, created_by in rows:
        new_dataset = _replace_catalog_refs(dataset or "", old_catalog, new_catalog)
        new_rule_sql = _replace_catalog_refs(rule_sql or "", old_catalog, new_catalog)

        if new_dataset == (dataset or "") and new_rule_sql == (rule_sql or ""):
            continue

        esc = lambda s: (s or "").replace("\\", "\\\\").replace("'", "\\'")
        try:
            run_sql(
                f"""
                MERGE INTO {target_rules_tbl} AS t
                USING (
                    SELECT
                        '{esc(rid)}' AS rule_id,
                        '{esc(rule_name)}' AS rule_name,
                        '{esc(new_dataset)}' AS dataset,
                        '{esc(rule_type)}' AS rule_type,
                        '{esc(new_rule_sql)}' AS rule_sql,
                        '{esc(severity)}' AS severity,
                        '{esc(category)}' AS category,
                        '{esc(owner)}' AS owner,
                        {str(bool(active)).lower()} AS active,
                        '{esc(created_by or "streamlit")}' AS created_by
                ) AS s
                ON t.rule_id = s.rule_id
                WHEN MATCHED THEN UPDATE SET
                    rule_name=s.rule_name,
                    dataset=s.dataset,
                    rule_type=s.rule_type,
                    rule_sql=s.rule_sql,
                    severity=s.severity,
                    category=s.category,
                    owner=s.owner,
                    active=s.active,
                    updated_by='promotion',
                    updated_at=current_timestamp()
                WHEN NOT MATCHED THEN INSERT (
                    rule_id, rule_name, dataset, rule_type, rule_sql, severity,
                    category, owner, active, created_by, created_at, updated_by, updated_at
                ) VALUES (
                    s.rule_id, s.rule_name, s.dataset, s.rule_type, s.rule_sql, s.severity,
                    s.category, s.owner, s.active, s.created_by, current_timestamp(), 'promotion', current_timestamp()
                )
                """,
                fetch=False,
            )
            log_audit(
                rid,
                "CATALOG_REMAP",
                field_changed="dataset,rule_sql",
                old_value=f"{old_catalog} ({source_env})",
                new_value=f"{new_catalog} ({target_env})",
            )
            updated += 1
        except Exception as e:
            errors.append(f"{rid}: {e}")

    return checked, updated, errors
