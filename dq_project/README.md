# 🛡️ Data Quality Validation Framework — End-to-End Project
# ══════════════════════════════════════════════════════════════

## Architecture

```
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
│                                        │  data_quality.rules         │    │
│                                        │  data_quality.results       │    │
│                                        │  data_quality.rule_audit    │    │
│                                        │  data_quality.alert_config  │    │
│                                        │  data_quality.alert_log     │    │
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

```
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
Run `backend/01_create_tables.sql` in a Databricks SQL editor or notebook.

### Step 2: Launch the Streamlit app
```bash
cd streamlit_app
pip install -r requirements.txt
streamlit run app.py
```

### Step 3: Schedule the validation runner
Import `notebooks/dq_validation_runner.py` into Databricks and schedule as a Workflow.
