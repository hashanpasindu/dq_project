# Data Quality Validation Framework

## Overview

This project provides an end-to-end Data Quality framework with:

- A Streamlit app to build, test, promote, and manage DQ rules
- Databricks backend Delta tables for rules, results, audit, and alerts
- A Databricks validation runner notebook for scheduled execution

## Architecture

```text
Local Machine                                              Databricks Workspace
┌──────────────────────────────┐                          ┌──────────────────────────────────────────────┐
│ Streamlit App                │                          │ SQL Warehouse                                │
│ - Build Rules                │   PAT + HTTP Path        │ - Executes rule SQL                          │
│ - Manage Rules               │─────────────────────────>│ - Serves metadata and catalogs               │
│ - Run History / Audit        │<─────────────────────────│ - Handles reads/writes to UC tables          │
└───────────────┬──────────────┘                          └───────────────────┬──────────────────────────┘
                │                                                           │
                │                                                           │
                │                                         Unity Catalog: data_quality
                │                                         ┌──────────────────────────────────────────────┐
                │                                         │ Schemas:                                     │
                │                                         │ - dev_rules                                  │
                │                                         │ - test_rules                                 │
                │                                         │ - prod_rules                                 │
                │                                         │                                              │
                │                                         │ Tables per schema:                           │
                │                                         │ - rules                                      │
                │                                         │ - results                                    │
                │                                         │ - rule_audit                                 │
                │                                         │ - alert_config                               │
                │                                         │ - alert_log                                  │
                │                                         └───────────────────┬──────────────────────────┘
                │                                                             │
                │                                                             │
                │                               Databricks Workflow (scheduled)
                │                          ┌──────────────────────────────────────────────┐
                └─────────────────────────>│ notebooks/dq_validation_runner.py           │
                                           │ - Reads active rules for selected env        │
                                           │ - Executes checks                            │
                                           │ - Writes results + audit updates             │
                                           │ - Triggers alerts (Email / Teams)            │
                                           └──────────────────────────────────────────────┘
```

### Environment Routing

- `dq_env=dev` -> `data_quality.dev_rules.*`
- `dq_env=test` -> `data_quality.test_rules.*`
- `dq_env=prod` -> `data_quality.prod_rules.*`

## Current Backend Model

Backend objects are organized in a dedicated Unity Catalog catalog named `data_quality`, with separate schemas per environment:

- `data_quality.dev_rules`
- `data_quality.test_rules`
- `data_quality.prod_rules`

Each schema contains:

- `rules`
- `results`
- `rule_audit`
- `alert_config`
- `alert_log`

## Project Structure

```text
dq_project/
├── README.md
├── backend/
│   ├── 01_create_tables.sql
│   ├── 02_seed_sample_rules.sql
│   └── 03_useful_queries.sql
├── notebooks/
│   ├── dq_setup_bootstrap.py
│   └── dq_validation_runner.py
├── streamlit_app/
│   ├── app.py
│   ├── config.py
│   ├── db.py
│   ├── requirements.txt
│   └── templates.py
└── docs/
    └── table_dictionary.md
```

## Quick Start

### 1. Create `data_quality` catalog first

The app does not auto-create the catalog anymore.

Create `data_quality` in Databricks Catalog Explorer (UI), or create it with explicit managed location:

```sql
CREATE CATALOG data_quality
MANAGED LOCATION 'abfss://<container>@<storage-account>.dfs.core.windows.net/<path>';
```

### 2. Start Streamlit app

```bash
cd streamlit_app
pip install -r requirements.txt
streamlit run app.py
```

### 3. Connect and bootstrap

In the Streamlit sidebar, connect to your Databricks SQL Warehouse.

On first successful connect, the app creates required schemas/tables for:

- `dev_rules`
- `test_rules`
- `prod_rules`

## Build Rules Behavior

- Build Rules catalog picker only shows catalogs containing `dev`.
- For custom rules, only a single read-only SQL query is allowed.
- Allowed custom SQL starts with `SELECT` or `WITH`.
- Disallowed keywords include `DELETE`, `UPDATE`, `INSERT`, `MERGE`, `DROP`, `ALTER`, and other non-read-only operations.

## Manage Rules Behavior

Manage Rules supports environment-specific operations.

- Use `Manage Environment` selector to manage `DEV`, `TEST`, or `PROD` separately.
- List, enable/disable, and delete are scoped to the selected environment.

### Promotion

Promotion is done from the Manage Rules page using dropdown catalog selectors.

Allowed promotion paths only:

- `dev` -> `uat/test`
- `uat/test` -> `prod`

During promotion:

- Selected rules are copied/merged from source env rules table into target env rules table.
- Catalog references in `dataset` and `rule_sql` are remapped from source catalog to target catalog.

## Validation Runner Notebook

Notebook: `notebooks/dq_validation_runner.py`

Workflow parameters:

- `dq_backend_catalog` (default: `data_quality`)
- `dq_env` (`dev`, `test`, or `prod`)
- `pipeline_tables` (optional)

Runner behavior:

- Reads active rules from `<dq_backend_catalog>.<env_schema>.rules`
- Executes each rule SQL
- Writes results to `<dq_backend_catalog>.<env_schema>.results`
- Updates rule run status (`last_run_at`, `last_run_passed`)
- Sends alerts using `<dq_backend_catalog>.<env_schema>.alert_config`
- Logs alert outcomes in `<dq_backend_catalog>.<env_schema>.alert_log`

`pipeline_tables` accepts comma/semicolon/newline-separated table names to run only matching rules.

## Notes

- If sidebar is hidden, refresh the page; app defaults sidebar to expanded.
- If SSL cert errors occur locally, provide a trusted CA path in sidebar connection settings.
