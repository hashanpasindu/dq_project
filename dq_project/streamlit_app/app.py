"""
🛡️ DQ Rule Builder — Streamlit App
═══════════════════════════════════════
streamlit run app.py
"""

import streamlit as st
import uuid
import time
import re
from config import load_config, save_config
from db import (
    test_connection, discover_catalogs, discover_schemas, discover_tables_in_schema,
    discover_columns, get_table_sample, ensure_dq_schema,
    push_rule, toggle_rule, delete_rule, get_all_rules, test_rule,
    get_latest_run, get_daily_trend, get_audit_log, remap_rule_catalog_references,
)
from templates import TEMPLATES

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="DQ Rule Builder",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stApp { background: #0b1120; }
    section[data-testid="stSidebar"] {
        background: #0f1729; border-right: 1px solid #1c2940;
    }
    .block-container { padding-top: 1.5rem; }
    #MainMenu, footer { visibility: hidden; }

    .sql-box {
        background: #030712; border: 1px solid #1e40af30;
        border-radius: 10px; padding: 14px 18px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px; color: #7dd3fc; line-height: 1.7;
        word-break: break-all; margin: 8px 0;
    }
    .card {
        background: #111827; border: 1px solid #1f2937;
        border-radius: 12px; padding: 16px 20px; margin-bottom: 10px;
    }
    .card-pass {
        background: #071a12; border: 1px solid #16a34a50;
        border-radius: 10px; padding: 12px 16px; margin: 6px 0;
    }
    .card-fail {
        background: #1a0808; border: 1px solid #dc262650;
        border-radius: 10px; padding: 12px 16px; margin: 6px 0;
    }
    .stat-box {
        text-align: center; padding: 18px 12px;
        border-radius: 12px; background: #111827;
        border: 1px solid #1f2937;
    }
    .stat-num { font-size: 36px; font-weight: 800; }
    .stat-label { font-size: 12px; color: #64748b; margin-top: 4px;
                  text-transform: uppercase; letter-spacing: 1px; }
    .sev-critical { background: #dc262615; color: #ef4444; border: 1px solid #ef444430;
                    padding: 2px 10px; border-radius: 6px; font-size: 11px;
                    font-weight: 800; text-transform: uppercase; }
    .sev-warning { background: #f59e0b15; color: #f59e0b; border: 1px solid #f59e0b30;
                   padding: 2px 10px; border-radius: 6px; font-size: 11px;
                   font-weight: 800; text-transform: uppercase; }
    .sev-info { background: #64748b15; color: #94a3b8; border: 1px solid #94a3b830;
                padding: 2px 10px; border-radius: 6px; font-size: 11px;
                font-weight: 800; text-transform: uppercase; }
    .tag { background: #1e293b; color: #94a3b8; padding: 3px 10px;
           border-radius: 6px; font-size: 12px; font-family: monospace; }
</style>
""", unsafe_allow_html=True)


# ─── Session State Init ─────────────────────────────────────────────────────
if "connected" not in st.session_state:
    st.session_state.connected = False
    st.session_state.tables = []
    st.session_state.catalogs = []
    st.session_state.schema_cache = {}
    st.session_state.table_cache = {}
    st.session_state.col_cache = {}
    st.session_state.rule_queue = []
    st.session_state.saved_config = load_config()


# ─── Helper ──────────────────────────────────────────────────────────────────
def get_cols(table_name):
    if table_name not in st.session_state.col_cache:
        st.session_state.col_cache[table_name] = discover_columns(table_name)
    return st.session_state.col_cache[table_name]


def get_schemas_for_catalog(catalog_name):
    if not catalog_name:
        return []
    if catalog_name not in st.session_state.schema_cache:
        cfg = st.session_state.dbx_config
        st.session_state.schema_cache[catalog_name] = discover_schemas(
            cfg["hostname"],
            cfg["http_path"],
            cfg["token"],
            catalog_name,
            cfg.get("tls_ca_file", ""),
        )
    return st.session_state.schema_cache.get(catalog_name, [])


def get_tables_for_scope(catalog_name, schema_name):
    if not (catalog_name and schema_name):
        return []
    cache_key = f"{catalog_name}.{schema_name}"
    if cache_key not in st.session_state.table_cache:
        cfg = st.session_state.dbx_config
        st.session_state.table_cache[cache_key] = discover_tables_in_schema(
            cfg["hostname"],
            cfg["http_path"],
            cfg["token"],
            catalog_name,
            schema_name,
            cfg.get("tls_ca_file", ""),
        )
    return st.session_state.table_cache.get(cache_key, [])


def validate_custom_sql(sql_text: str) -> tuple[bool, str | None]:
    """Allow only read-only SQL queries for custom rules."""
    text = (sql_text or "").strip()
    if not text:
        return False, "Custom SQL is required"

    # Allow one optional trailing semicolon, but block multi-statement SQL.
    normalized = text.rstrip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if ";" in normalized:
        return False, "Only a single SQL statement is allowed"

    lower = normalized.lower()
    if not (lower.startswith("select") or lower.startswith("with")):
        return False, "Only SELECT queries are allowed for custom rules"

    blocked = [
        "insert", "update", "delete", "merge", "drop", "alter",
        "create", "truncate", "grant", "revoke", "call", "execute",
    ]
    for kw in blocked:
        if re.search(rf"\\b{kw}\\b", lower):
            return False, f"Disallowed SQL keyword: {kw.upper()}"

    return True, None


# ═════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("# 🛡️ DQ Rule Builder")
    st.caption("Local → Databricks")
    st.divider()

    saved = st.session_state.saved_config
    hostname = st.text_input("Server Hostname", value=saved.get("hostname", ""),
                             placeholder="adb-xxxx.azuredatabricks.net")
    http_path = st.text_input("HTTP Path", value=saved.get("http_path", ""),
                              placeholder="/sql/1.0/warehouses/xxx")
    dq_catalog = st.text_input("DQ Catalog", value=saved.get("dq_catalog", ""),
                               placeholder="hive_metastore")
    tls_ca_file = st.text_input(
        "Trusted CA file path (optional)",
        value=saved.get("tls_ca_file", ""),
        placeholder="/path/to/corporate-root-ca.pem",
    )
    token = st.text_input("PAT Token", value=saved.get("token", ""),
                          type="password", placeholder="dapi...")
    remember = st.checkbox("Remember connection", value=bool(saved))

    if st.session_state.connected:
        st.success(f"Connected · {len(st.session_state.catalogs)} catalogs")
        current_dq_catalog = st.session_state.dbx_config.get("dq_catalog", "")
        st.caption(f"Rules Catalog: {current_dq_catalog}")
        if st.session_state.dbx_config.get("tls_ca_file"):
            st.caption(f"TLS CA: {st.session_state.dbx_config.get('tls_ca_file')}")

        if dq_catalog.strip() and dq_catalog.strip() != current_dq_catalog:
            if st.button("🧭 Apply Rules Catalog", use_container_width=True):
                with st.spinner("Switching rules catalog..."):
                    schema_ok, schema_errors = ensure_dq_schema(dq_catalog.strip())
                if not schema_ok:
                    st.error("Failed to switch rules catalog.")
                    with st.expander("Show catalog switch errors"):
                        for err in schema_errors:
                            st.code(err)
                else:
                    st.session_state.dbx_config["dq_catalog"] = dq_catalog.strip()
                    if remember:
                        save_config(st.session_state.dbx_config)
                        st.session_state.saved_config = st.session_state.dbx_config
                    st.success(f"Rules catalog switched to: {dq_catalog.strip()}")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 Refresh", use_container_width=True):
                st.session_state.col_cache = {}
                st.session_state.schema_cache = {}
                st.session_state.table_cache = {}
                st.session_state.tables = []
                cfg = st.session_state.dbx_config
                with st.spinner("Refreshing..."):
                    st.session_state.catalogs = discover_catalogs(
                        cfg["hostname"],
                        cfg["http_path"],
                        cfg["token"],
                        cfg.get("tls_ca_file", ""),
                    )
                st.rerun()
        with c2:
            if st.button("🔌 Disconnect", use_container_width=True):
                st.session_state.connected = False
                st.session_state.tables = []
                st.session_state.catalogs = []
                st.session_state.schema_cache = {}
                st.session_state.table_cache = {}
                st.rerun()
    else:
        if st.button("⚡ Connect", type="primary", use_container_width=True):
            if not all([hostname, http_path, token]):
                st.error("Fill in all fields")
            elif not dq_catalog.strip():
                st.error("DQ Catalog is required")
            else:
                with st.spinner("Connecting..."):
                    ok, msg = test_connection(
                        hostname,
                        http_path,
                        token,
                        tls_ca_file=tls_ca_file,
                    )
                if not ok:
                    st.error(f"Failed: {msg}")
                else:
                    cfg = {
                        "hostname": hostname,
                        "http_path": http_path,
                        "token": token,
                        "dq_catalog": dq_catalog.strip(),
                        "tls_ca_file": tls_ca_file.strip(),
                    }
                    st.session_state.dbx_config = cfg
                    with st.spinner("Loading catalogs & creating DQ schema..."):
                        st.session_state.catalogs = discover_catalogs(
                            hostname,
                            http_path,
                            token,
                            tls_ca_file=tls_ca_file,
                        )
                        st.session_state.tables = []
                        st.session_state.schema_cache = {}
                        st.session_state.table_cache = {}
                        schema_ok, schema_errors = ensure_dq_schema(cfg["dq_catalog"])
                    if not schema_ok:
                        st.error("Connected, but failed to create one or more backend tables.")
                        with st.expander("Show bootstrap errors"):
                            for err in schema_errors:
                                st.code(err)
                    else:
                        st.session_state.connected = True
                        if remember:
                            save_config(cfg)
                            st.session_state.saved_config = cfg
                        st.rerun()

    st.divider()
    page = st.radio("Navigate", [
        "🏗️ Build Rules",
        "📋 Manage Rules",
        "📊 Run History",
        "📝 Audit Log",
    ], label_visibility="collapsed")

    # Queue indicator
    q = st.session_state.rule_queue
    if q:
        st.divider()
        st.markdown(f"### 📝 Queue: {len(q)}")
        if st.button("🚀 Push All", type="primary", use_container_width=True):
            bar = st.progress(0)
            ok_n, errs = 0, []
            for i, r in enumerate(q):
                ok, err = push_rule(r)
                ok_n += 1 if ok else 0
                if err:
                    errs.append(f"{r['rule_id']}: {err}")
                bar.progress((i + 1) / len(q))
            bar.empty()
            if errs:
                st.warning(f"✅ {ok_n} ok · ❌ {len(errs)} failed")
            else:
                st.success(f"✅ All {ok_n} pushed!")
                st.session_state.rule_queue = []
                time.sleep(0.5)
                st.rerun()
        if st.button("🗑️ Clear Queue", use_container_width=True):
            st.session_state.rule_queue = []
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# GUARD — must be connected
# ═════════════════════════════════════════════════════════════════════════════
if not st.session_state.connected:
    st.markdown("# 🛡️ Data Quality Rule Builder")
    st.info("👈 Connect to your Databricks workspace using the sidebar to get started.")
    st.markdown("""
    **Backend tables created automatically on connect (env-scoped):**

    | Table | Purpose |
    |-------|---------|
    | `data_quality.rules_<env>` | Rule definitions (what to check) |
    | `data_quality.results_<env>` | Validation run history (append-only) |
    | `data_quality.rule_audit_<env>` | Who changed what, when |
    | `data_quality.alert_config_<env>` | Alert channel settings |
    | `data_quality.alert_log_<env>` | Alert delivery log |
    """)
    st.stop()


# ═════════════════════════════════════════════════════════════════════════════
# PAGE: BUILD RULES
# ═════════════════════════════════════════════════════════════════════════════
if page == "🏗️ Build Rules":
    st.markdown("## 🏗️ Build a New Rule")

    tmpl_name = st.selectbox("**Rule Type**", list(TEMPLATES.keys()),
                             format_func=lambda x: f"{x} — {TEMPLATES[x]['desc']}")
    tmpl = TEMPLATES[tmpl_name]
    st.divider()

    # Metadata
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        rule_name = st.text_input("📌 Rule Name", placeholder="e.g. Orders daily freshness")
    with c2:
        severity = st.selectbox("⚠️ Severity", ["critical", "warning", "info"])
    with c3:
        owner = st.text_input("👤 Owner", placeholder="data-eng")
    with c4:
        category = st.text_input("📁 Category", placeholder="sales")
    pipeline_name = st.text_input(
        "🔗 Pipeline Name (optional)",
        placeholder="e.g. orders_daily — the runner will scope checks to this pipeline when pipeline_name is passed",
        help="Tag this rule with a pipeline name. Pass the same name as the `pipeline_name` widget when triggering the validation runner at the end of that pipeline.",
    )

    st.divider()

    # Table & Columns
    selected_table = ""
    columns = []
    available_tables = []
    if "custom_sql" not in tmpl["fields"]:
        dev_catalogs = [c for c in st.session_state.catalogs if "dev" in c.lower()]
        if not dev_catalogs:
            st.warning("No catalogs with 'dev' in the name are available for rule creation.")
        selected_catalog = st.selectbox(
            "**🗂️ Select Catalog**",
            [""] + dev_catalogs,
            format_func=lambda x: "— Pick a catalog —" if not x else x,
        )

        schema_options = []
        selected_schema = ""
        if selected_catalog:
            with st.spinner("Loading schemas..."):
                schema_options = get_schemas_for_catalog(selected_catalog)
            selected_schema = st.selectbox(
                "**🧩 Select Schema**",
                [""] + schema_options,
                format_func=lambda x: "— Pick a schema —" if not x else x,
            )

        if selected_catalog and selected_schema:
            with st.spinner("Loading tables..."):
                available_tables = get_tables_for_scope(selected_catalog, selected_schema)
            st.session_state.tables = available_tables

        selected_table = st.selectbox(
            "**🗄️ Select Table**", [""] + available_tables,
            format_func=lambda x: "— Pick a table (type to search) —" if not x else x,
        )
        if selected_table:
            with st.spinner(f"Loading columns..."):
                columns = get_cols(selected_table)
            if columns:
                st.caption(f"{len(columns)} columns found")

            with st.expander("👀 Sample Data (2 rows)", expanded=False):
                s_cols, s_rows, s_err = get_table_sample(selected_table, limit=2)
                if s_err:
                    st.warning(f"Could not load sample rows: {s_err}")
                elif not s_rows:
                    st.info("No records found in this table.")
                else:
                    preview_rows = [dict(zip(s_cols, r)) for r in s_rows]
                    st.dataframe(preview_rows, use_container_width=True, hide_index=True)

    # Dynamic fields
    fv = {}
    if columns or "custom_sql" in tmpl["fields"]:
        col_disp = [f"{c['name']}  ({c['type']})" for c in columns]
        col_names = [c["name"] for c in columns]
        f1, f2 = st.columns(2)

        for i, field in enumerate(tmpl["fields"]):
            t = f1 if i % 2 == 0 else f2
            if field in ("date_column", "target_column"):
                label = "📅 Date Column" if field == "date_column" else "🎯 Target Column"
                with t:
                    idx = st.selectbox(label, range(len(col_disp)),
                                       format_func=lambda i: col_disp[i], key=field)
                    fv[field] = col_names[idx]
            elif field == "interval_days":
                with t:
                    fv[field] = str(st.number_input("📆 Max Days Old", min_value=1, value=1))
            elif field == "min_rows":
                with t:
                    fv[field] = str(st.number_input("🔢 Min Rows", min_value=0, value=0))
            elif field == "min_val":
                with t:
                    fv[field] = str(st.number_input("⬇️ Min", value=0))
            elif field == "max_val":
                with t:
                    fv[field] = str(st.number_input("⬆️ Max", value=100))
            elif field == "allowed_values":
                fv[field] = st.text_input("📋 Allowed Values (comma-separated)",
                                          placeholder="pending, shipped, delivered")
            elif field == "parent_table":
                with t:
                    parent_catalog = st.selectbox(
                        "🗂️ Parent Catalog",
                        [""] + st.session_state.catalogs,
                        format_func=lambda x: "— Pick catalog —" if not x else x,
                        key="p_cat",
                    )
                    parent_schemas = []
                    parent_schema = ""
                    if parent_catalog:
                        with st.spinner("Loading parent schemas..."):
                            parent_schemas = get_schemas_for_catalog(parent_catalog)
                        parent_schema = st.selectbox(
                            "🧩 Parent Schema",
                            [""] + parent_schemas,
                            format_func=lambda x: "— Pick schema —" if not x else x,
                            key="p_sch",
                        )

                    parent_tables = []
                    if parent_catalog and parent_schema:
                        with st.spinner("Loading parent tables..."):
                            parent_tables = get_tables_for_scope(parent_catalog, parent_schema)

                    fv[field] = st.selectbox(
                        "🗄️ Parent Table",
                        [""] + parent_tables,
                        format_func=lambda x: "— Select —" if not x else x,
                        key="ptbl",
                    )
            elif field == "parent_column":
                pt = fv.get("parent_table", "")
                with t:
                    if pt:
                        pc = get_cols(pt)
                        pc_d = [f"{c['name']} ({c['type']})" for c in pc]
                        pc_n = [c["name"] for c in pc]
                        if pc:
                            pi = st.selectbox("🔑 Parent Column", range(len(pc_d)),
                                              format_func=lambda i: pc_d[i], key="pcol")
                            fv[field] = pc_n[pi]
                        else:
                            fv[field] = st.text_input("🔑 Parent Column", placeholder="id")
                    else:
                        fv[field] = st.text_input("🔑 Parent Column", placeholder="id")
            elif field == "custom_sql":
                fv[field] = st.text_area("✏️ SQL (must return TRUE/FALSE)",
                                         placeholder="SELECT COUNT(*) = 0 FROM ...", height=120)

    # Build SQL
    gen_sql = ""
    try:
        args = [selected_table] + [fv.get(f, "") for f in tmpl["fields"]]
        if tmpl["fields"] == ["custom_sql"]:
            args = [selected_table, fv.get("custom_sql", "")]
        gen_sql = tmpl["sql"](*args)
    except Exception:
        pass

    custom_sql_err = None
    if tmpl["fields"] == ["custom_sql"]:
        ok_sql, custom_sql_err = validate_custom_sql(fv.get("custom_sql", ""))
        if not ok_sql:
            st.error(custom_sql_err)

    if gen_sql:
        st.markdown("---")
        st.markdown("**Generated SQL:**")
        st.markdown(f'<div class="sql-box">{gen_sql}</div>', unsafe_allow_html=True)

    can_go = bool(gen_sql) and (bool(selected_table) or "custom_sql" in tmpl["fields"])
    if tmpl["fields"] == ["custom_sql"] and custom_sql_err:
        can_go = False
    st.markdown("---")
    a1, a2, a3 = st.columns(3)

    with a1:
        if st.button("🧪 Test Rule", disabled=not can_go, use_container_width=True):
            with st.spinner("Executing..."):
                passed, elapsed, err = test_rule(gen_sql)
            if err:
                st.markdown(f'<div class="card-fail">❌ ERROR ({elapsed}s): {err}</div>', unsafe_allow_html=True)
            elif passed:
                st.markdown(f'<div class="card-pass">✅ PASSED in {elapsed}s</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="card-fail">❌ FAILED in {elapsed}s</div>', unsafe_allow_html=True)

    with a2:
        if st.button("➕ Add to Queue", disabled=not can_go, use_container_width=True):
            rid = f"R{len(st.session_state.rule_queue) + 1:03d}"
            st.session_state.rule_queue.append({
                "rule_id": rid, "dataset": selected_table or "",
                "rule_name": rule_name or tmpl_name.split(" ", 1)[1],
                "rule_type": tmpl["key"], "rule_sql": gen_sql,
                "severity": severity, "owner": owner, "category": category,
                "tags": f"pipeline:{pipeline_name.strip()}" if pipeline_name.strip() else "",
                "created_by": "streamlit",
            })
            st.toast(f"✅ {rid} queued")
            st.rerun()

    with a3:
        if st.button("🚀 Push Now", disabled=not can_go, type="primary", use_container_width=True):
            rid = f"R_{uuid.uuid4().hex[:6].upper()}"
            rule = {
                "rule_id": rid, "dataset": selected_table or "",
                "rule_name": rule_name or tmpl_name.split(" ", 1)[1],
                "rule_type": tmpl["key"], "rule_sql": gen_sql,
                "severity": severity, "owner": owner, "category": category,
                "tags": f"pipeline:{pipeline_name.strip()}" if pipeline_name.strip() else "",
                "created_by": "streamlit",
            }
            with st.spinner("Pushing..."):
                ok, err = push_rule(rule)
            if ok:
                st.toast(f"✅ {rid} pushed!")
                st.balloons()
            else:
                st.error(f"Failed: {err}")

    # Show queue
    if st.session_state.rule_queue:
        st.markdown("---")
        st.markdown(f"### 📝 Queue ({len(st.session_state.rule_queue)})")
        for r in st.session_state.rule_queue:
            sev = f'<span class="sev-{r["severity"]}">{r["severity"]}</span>'
            st.markdown(
                f'<div class="card"><span class="tag">{r["rule_id"]}</span> {sev} '
                f'<b style="color:#e2e8f0;">{r["rule_name"]}</b><br>'
                f'<span style="color:#64748b;font-size:12px;">{r["dataset"]} · {r["rule_type"]}</span>'
                f'<div class="sql-box" style="font-size:11px;margin-top:6px;">'
                f'{r["rule_sql"][:200]}</div></div>', unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# PAGE: MANAGE RULES
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📋 Manage Rules":
    st.markdown("## 📋 Manage Existing Rules")

    def env_from_catalog_name(catalog_name: str) -> str:
        c = (catalog_name or "").lower()
        if "prod" in c:
            return "prod"
        if "uat" in c or "test" in c:
            return "test"
        return "dev"

    env_options = ["dev", "test", "prod"]
    default_env = env_from_catalog_name(st.session_state.dbx_config.get("dq_catalog", ""))
    default_env_idx = env_options.index(default_env) if default_env in env_options else 0
    manage_env = st.selectbox(
        "🧭 Manage Environment",
        env_options,
        index=default_env_idx,
        format_func=lambda x: x.upper(),
        help="Manage rules in DEV, TEST/UAT, or PROD separately",
    )

    try:
        _, rows = get_all_rules(manage_env)
    except Exception as e:
        st.error(f"Failed: {e}")
        st.stop()

    if not rows:
        st.info(f"No rules found in {manage_env.upper()}. Go to **Build Rules** to create your first rule.")
        st.stop()

    st.markdown("### 🚚 Promote Rules: Dev → Test → Prod")
    st.caption("Search rules by table name, pick the rules to promote, then replace the data catalog inside dataset and rule SQL.")

    promote_table_options = sorted({str(row[1] or "") for row in rows if row[1]})
    selected_promote_table = st.selectbox(
        "🔎 Promotion Table Filter",
        ["All tables"] + promote_table_options,
        help="Type to search table names",
        key="promote_table_search",
    )

    promote_candidates = []
    for row in rows:
        rid, ds, rname, rtype, rsql, sev, cat, own, is_active, last_at, last_ok, tags = row
        dataset = str(ds or "")
        if selected_promote_table != "All tables" and dataset != selected_promote_table:
            continue
        promote_candidates.append((rid, ds, rname))

    promote_options = [
        f"{rid} | {dataset} | {rule_name}"
        for rid, dataset, rule_name in promote_candidates
    ]
    promote_option_to_id = {
        f"{rid} | {dataset} | {rule_name}": rid
        for rid, dataset, rule_name in promote_candidates
    }
    rule_lookup = {row[0]: row for row in rows}

    selected_promotion_options = st.multiselect(
        "Select Rules To Promote",
        options=promote_options,
        placeholder="Choose one or more rules",
    )

    promote_catalog_options = sorted(set(st.session_state.catalogs))

    def promotion_env_key(catalog_name: str) -> str:
        c = (catalog_name or "").lower()
        if "prod" in c:
            return "prod"
        if "uat" in c or "test" in c:
            return "test"
        if "dev" in c:
            return "dev"
        return "unknown"

    env_catalog_options = [
        c for c in promote_catalog_options
        if promotion_env_key(c) in {"dev", "test", "prod"}
    ]

    promotion_mode = st.selectbox(
        "Promotion Type",
        ["dev -> uat/test", "uat/test -> prod"],
        key="promotion_mode",
        help="Select which promotion flow you want to run",
    )

    allowed_source_env = "dev" if promotion_mode == "dev -> uat/test" else "test"
    allowed_target_env = "test" if promotion_mode == "dev -> uat/test" else "prod"

    source_catalog_options = [
        c for c in env_catalog_options
        if promotion_env_key(c) == allowed_source_env
    ]

    p1, p2 = st.columns(2)
    with p1:
        source_catalog = st.selectbox(
            "Source Data Catalog",
            [""] + source_catalog_options,
            key="promote_src",
            format_func=lambda x: "— Select source catalog —" if not x else x,
            help="Only catalogs valid for the selected promotion type are shown",
        )
    with p2:
        source_env = promotion_env_key(source_catalog)
        if source_env == allowed_source_env:
            allowed_targets = [
                c for c in env_catalog_options
                if promotion_env_key(c) == allowed_target_env
            ]
        else:
            allowed_targets = []

        target_catalog = st.selectbox(
            "Target Data Catalog",
            [""] + allowed_targets,
            key="promote_tgt",
            format_func=lambda x: "— Select target catalog —" if not x else x,
            help="Targets follow the selected promotion type",
        )

    selected_rule_ids = [promote_option_to_id[x] for x in selected_promotion_options]

    if selected_rule_ids:
        preview_rows = []
        source_catalog_lc = source_catalog.strip().lower()
        for rule_id in selected_rule_ids:
            row = rule_lookup.get(rule_id)
            if not row:
                continue
            dataset = str(row[1] or "")
            rule_sql = str(row[4] or "")
            source_in_dataset = bool(source_catalog_lc) and f"{source_catalog_lc}." in dataset.lower()
            source_in_sql = bool(source_catalog_lc) and f"{source_catalog_lc}." in rule_sql.lower()
            preview_rows.append({
                "Rule ID": rule_id,
                "Dataset": dataset,
                "Source In Dataset": source_in_dataset,
                "Source In SQL": source_in_sql,
            })

        st.dataframe(preview_rows, use_container_width=True, hide_index=True)
        if source_catalog.strip() and not any(r["Source In Dataset"] or r["Source In SQL"] for r in preview_rows):
            st.warning("The selected rules do not currently contain the source catalog. For a dev to test promotion, source should be the current catalog in the rule and target should be the new catalog.")

    if st.button("🔁 Remap Catalog In Selected Rules", type="primary", use_container_width=True):
        src_env = promotion_env_key(source_catalog)
        tgt_env = promotion_env_key(target_catalog)
        allowed_transition = (src_env == "dev" and tgt_env == "test") or (src_env == "test" and tgt_env == "prod")

        if not source_catalog or not target_catalog:
            st.warning("Select both source and target catalogs before promotion.")
        elif not allowed_transition:
            st.warning("Only these promotions are allowed: dev -> uat/test, uat/test -> prod.")
        else:
            checked, updated, errs = remap_rule_catalog_references(
                source_catalog,
                target_catalog,
                rule_ids=selected_rule_ids,
            )
            if errs:
                st.warning(f"Checked {checked} rules · Updated {updated} · Errors {len(errs)}")
                with st.expander("Show remap errors"):
                    for e in errs:
                        st.code(e)
            elif checked > 0 and updated == 0:
                st.warning("Checked selected rules, but none contained the source catalog to replace. Verify source and target order.")
            else:
                st.success(f"Checked {checked} rules · Updated {updated}")

    if not promote_candidates:
        st.info("No rules matched the table-name search for promotion.")

    st.divider()

    search_env = st.selectbox(
        "🔎 Search Environment",
        env_options,
        index=env_options.index(manage_env),
        format_func=lambda x: x.upper(),
        help="Search and manage rules in the selected environment",
        key="search_rules_env",
    )

    try:
        _, search_rows = get_all_rules(search_env)
    except Exception as e:
        st.error(f"Failed to load search environment rules: {e}")
        st.stop()

    if not search_rows:
        st.info(f"No rules found in {search_env.upper()} for search.")
        st.stop()

    total = len(search_rows)
    active = sum(1 for r in search_rows if r[8])
    crit = sum(1 for r in search_rows if r[5] == "critical" and r[8])

    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#60a5fa;">{total}</div><div class="stat-label">Total</div></div>', unsafe_allow_html=True)
    with s2:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#22c55e;">{active}</div><div class="stat-label">Active</div></div>', unsafe_allow_html=True)
    with s3:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#ef4444;">{crit}</div><div class="stat-label">Critical</div></div>', unsafe_allow_html=True)

    st.divider()

    rule_search_options = [
        f"{r[0]} | {r[2]} | {r[1]} | {r[3]}"
        for r in search_rows
    ]
    selected_rule_search = st.multiselect(
        "🔍 Search rules",
        options=rule_search_options,
        placeholder="Type to search and select rules",
    )
    selected_rule_ids_for_view = {opt.split(" | ", 1)[0] for opt in selected_rule_search}

    for row in search_rows:
        rid, ds, rname, rtype, rsql, sev, cat, own, is_active, last_at, last_ok, tags = row
        if selected_rule_ids_for_view and rid not in selected_rule_ids_for_view:
            continue

        dot = "🟢" if is_active else "⚪"
        sev_h = f'<span class="sev-{sev}">{sev}</span>'
        last_icon = "✅" if last_ok else ("❌" if last_ok is False else "—")
        _pl = next((t.strip()[9:] for t in (tags or "").split(",") if t.strip().startswith("pipeline:")), "")

        st.markdown(
            f'<div class="card">{dot} <span class="tag">{rid}</span> {sev_h} '
            f'<span style="color:#64748b;font-size:12px;">{rtype} · {cat or ""}</span> '
            f'<span style="float:right;">{last_icon}</span><br>'
            f'<b style="color:#f1f5f9;font-size:15px;">{rname}</b><br>'
            f'<span style="color:#64748b;font-size:12px;">{ds} · Owner: {own or "—"}'
            f'{(" · 🔗 " + _pl) if _pl else ""}</span>'
            f'<div class="sql-box" style="font-size:11px;margin-top:6px;">{rsql}</div></div>',
            unsafe_allow_html=True)

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("🧪 Test", key=f"t_{rid}", use_container_width=True):
                with st.spinner("Testing..."):
                    ok, el, err = test_rule(rsql)
                if err:
                    st.error(f"Error ({el}s): {err}")
                elif ok:
                    st.success(f"✅ Passed ({el}s)")
                else:
                    st.error(f"❌ Failed ({el}s)")
        with b2:
            lbl = "⏸️ Disable" if is_active else "▶️ Enable"
            if st.button(lbl, key=f"tog_{rid}", use_container_width=True):
                toggle_rule(rid, not is_active, search_env)
                st.rerun()
        with b3:
            if st.button("🗑️ Delete", key=f"del_{rid}", use_container_width=True):
                delete_rule(rid, search_env)
                st.toast(f"Deleted {rid}")
                st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# PAGE: RUN HISTORY
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📊 Run History":
    st.markdown("## 📊 Validation Run History")
    try:
        _, rows = get_latest_run()
    except Exception as e:
        st.error(f"Failed: {e}")
        st.stop()

    if not rows:
        st.info("No runs yet. Schedule the validation runner notebook.")
        st.stop()

    passed_n = sum(1 for r in rows if r[4])
    failed_n = sum(1 for r in rows if not r[4])
    total_t = sum(r[6] or 0 for r in rows)

    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#22c55e;">{passed_n}</div><div class="stat-label">Passed</div></div>', unsafe_allow_html=True)
    with m2:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#ef4444;">{failed_n}</div><div class="stat-label">Failed</div></div>', unsafe_allow_html=True)
    with m3:
        st.markdown(f'<div class="stat-box"><div class="stat-num" style="color:#60a5fa;">{total_t:.1f}s</div><div class="stat-label">Runtime</div></div>', unsafe_allow_html=True)

    st.divider()
    for row in rows:
        rid, ds, rname, sev, ok, err, dur, ts, runid = row
        icon = "✅" if ok else "❌"
        cls = "card-pass" if ok else "card-fail"
        sev_h = f'<span class="sev-{sev}">{sev}</span>'
        st.markdown(
            f'<div class="{cls}">{icon} <span class="tag">{rid}</span> {sev_h} '
            f'<b style="color:#e2e8f0;">{rname}</b>'
            f'<span style="float:right;color:#64748b;font-size:12px;">{dur or 0:.1f}s</span><br>'
            f'<span style="color:#64748b;font-size:12px;">{ds}{(" · " + str(err)) if err and not ok else ""}</span></div>',
            unsafe_allow_html=True)

    st.divider()
    st.markdown("### 📈 30-Day Trend")
    try:
        _, trend = get_daily_trend()
        if trend:
            import pandas as pd
            df = pd.DataFrame(trend, columns=["Date", "Critical", "Warnings", "Passed"])
            df["Date"] = pd.to_datetime(df["Date"])
            st.area_chart(df.set_index("Date"), color=["#ef4444", "#f59e0b", "#22c55e"])
    except Exception:
        st.info("Not enough data yet.")


# ═════════════════════════════════════════════════════════════════════════════
# PAGE: AUDIT LOG
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📝 Audit Log":
    st.markdown("## 📝 Rule Change Audit Log")
    try:
        _, rows = get_audit_log(100)
    except Exception as e:
        st.error(f"Failed: {e}")
        st.stop()

    if not rows:
        st.info("No audit entries yet. Changes will appear here as you create, edit, and delete rules.")
        st.stop()

    import pandas as pd
    df = pd.DataFrame(rows, columns=["Audit ID", "Rule ID", "Action", "Field", "Old", "New", "By", "At"])
    st.dataframe(df, use_container_width=True, hide_index=True)
