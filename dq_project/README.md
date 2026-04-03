# 🛡️ Data Quality Validation Framework — End-to-End Project

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                         DQ VALIDATION SYSTEM                            │
│                                                                          │
│   LOCAL MACHINE                        DATABRICKS WORKSPACE              │
│  ┌─────────────────┐                  ┌────────────────────────────┐     │
│  │  Streamlit App   │───── PAT ───────▶│  SQL Warehouse             │     │
│  │  (Rule Builder)  │  CRUD rules      │  (Executes all queries)    │     │
│  │  localhost:8501  │◀── results ──────│                            │     │
│  └─────────────────┘                  └────────────┬───────────────┘     │
│                                                     │                    │
│                                        ┌────────────▼───────────────┐    │
│                                        │  BACKEND DELTA TABLES       │    │
│                                        │  (Unity Catalog)            │    │
│                                        │                             │    │
│                                        │  data_quality.rules_<env>   │    │
│                                        │  data_quality.results_<env> │    │
│                                        │  data_quality.rule_audit_<env> │ │
│                                        │  data_quality.alert_config_<env>│ │
│                                        │  data_quality.alert_log_<env> │ │
│                                        └────────────┬───────────────┘    │
│                                                     │                    │
│                                        ┌────────────▼───────────────┐    │
│                                        │  VALIDATION RUNNER          │    │
│                                        │  (Scheduled Databricks Job) │    │
│                                        │  Reads rules → Executes     │    │
│                                        │  → Writes results           │    │
│                                        │  → Sends alerts             │    │
│                                        └────────────┬───────────────┘    │
│                                                     │                    │
│                                          ┌──────────┴──────────┐         │
│                                          ▼                    ▼          │
│                                   ┌────────────┐     ┌──────────────┐    │
│                                   │  MS Teams   │     │  Email       │    │
│                                   │  Alerts     │     │  Alerts      │    │
│                                   └────────────┘     └──────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

## Project Structure

```text
dq_project/
│
├── README.md                          ← You are here
│
├── backend/
│   ├── 01_create_tables.sql           ← All DDL — run once in Databricks
│   ├── 02_seed_sample_rules.sql       ← Example rules to get started
│   └── 03_useful_queries.sql          ← Dashboard & diagnostic queries
│
├── notebooks/
│   ├── dq_validation_runner.py        ← Scheduled job — runs all rules
│   └── dq_setup_bootstrap.py         ← One-click setup notebook
│
├── streamlit_app/
│   ├── app.py                         ← The Streamlit Rule Builder
│   ├── db.py                          ← Databricks connection & queries
│   ├── templates.py                   ← Rule template definitions
│   ├── config.py                      ← Local config management
│   └── requirements.txt               ← Python dependencies
│
└── docs/
    └── table_dictionary.md            ← Column-level documentation
```

## Quick Start

### Step 1: Create backend tables

Recommended: connect from the Streamlit app and let it auto-bootstrap tables in your selected catalog.

Notes:

- The app now creates environment-scoped tables based on catalog name:
- contains `dev` -> `_dev`
- contains `test` or `uat` -> `_uat`
- contains `prod` -> `_prod`
- Example tables: `data_quality.rules_dev`, `data_quality.results_uat`, `data_quality.alert_log_prod`.

Optional: you can still run `backend/01_create_tables.sql` manually for baseline setup.

### Step 2: Launch the Streamlit app

```bash
cd streamlit_app
pip install -r requirements.txt
streamlit run app.py
```

### Step 3: Schedule the validation runner

Import `notebooks/dq_validation_runner.py` into Databricks and schedule as a Workflow.

Required job parameter:

- `dq_catalog`: catalog to run against (for example `aa_data_dev`, `aa_data_dev_test`, `aa_data_prod`)

Runner behavior:

- Reads from `data_quality.rules_<env>` in the provided catalog
- Writes to `data_quality.results_<env>`
- Uses matching env-scoped alert and audit tables

## Promotion (Dev/Test/UAT/Prod)

Use the `Manage Rules` page in Streamlit:

1. Search by table name
2. Select specific rules to promote
3. Provide source catalog and target catalog
4. Run `Remap Catalog In Selected Rules`

Example:

- From: `aa_data_dev_test.gold.avaya_user_activity`
- To: `aa_data_dev.gold.avaya_user_activity`
- Source catalog: `aa_data_dev_test`
- Target catalog: `aa_data_dev`
