# 📖 Data Quality Framework — Table Dictionary

## Entity Relationship

```
                    ┌──────────────────────┐
                    │  data_quality.rules   │   Master rule definitions
                    │  (PK: rule_id)        │
                    └──────┬───────┬────────┘
                           │       │
              writes to ◄──┘       └──► read by
                    │                      │
    ┌───────────────▼──────┐   ┌───────────▼──────────────┐
    │ data_quality.         │   │ data_quality.results      │
    │ rule_audit            │   │ (FK: rule_id)             │
    │ (FK: rule_id)         │   │ Validation run log        │
    │ Change history        │   └───────────┬──────────────┘
    └──────────────────────┘                │
                                  triggers  │
                                            ▼
                    ┌──────────────────────────────────────┐
                    │ data_quality.alert_config             │
                    │ (PK: config_id)                       │
                    │ Where to send alerts                  │
                    └──────────────────┬───────────────────┘
                                       │
                              logs to   ▼
                    ┌──────────────────────────────────────┐
                    │ data_quality.alert_log                │
                    │ (FK: config_id, run_id)               │
                    │ Record of every alert sent            │
                    └──────────────────────────────────────┘
```

---

## Table 1: `data_quality.rules`

**Purpose**: Master table holding all DQ rule definitions. The Streamlit app writes here; the validation runner reads here.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| rule_id | STRING | NO | Unique rule identifier (PK). E.g. `R001` or `R_A1B2C3` |
| rule_name | STRING | NO | Human-readable name. E.g. "Orders daily freshness" |
| dataset | STRING | NO | Fully qualified table: `catalog.schema.table` |
| rule_type | STRING | NO | One of: `freshness`, `row_count`, `null_check`, `uniqueness`, `business_logic`, `referential`, `custom` |
| rule_sql | STRING | NO | SQL returning TRUE (pass) or FALSE (fail) |
| severity | STRING | NO | One of: `critical`, `warning`, `info` |
| category | STRING | YES | Business domain grouping: finance, sales, marketing, etc. |
| tags | STRING | YES | Comma-separated labels for filtering |
| owner | STRING | YES | Responsible team or person |
| notification_channel | STRING | YES | Override: `teams`, `email`, `slack`, `pagerduty` |
| schedule | STRING | NO | `daily` (default), `hourly`, `on_pipeline`, `manual` |
| depends_on | STRING | YES | Comma-separated rule_ids that must pass first |
| active | BOOLEAN | NO | True = enabled, False = disabled |
| last_run_at | TIMESTAMP | YES | When this rule was last executed |
| last_run_passed | BOOLEAN | YES | Whether the last execution passed |
| created_by | STRING | YES | Who created the rule |
| created_at | TIMESTAMP | NO | Creation timestamp |
| updated_by | STRING | YES | Who last modified |
| updated_at | TIMESTAMP | NO | Last modification timestamp |

---

## Table 2: `data_quality.results`

**Purpose**: Append-only log of every validation execution. One row per rule per run. Partitioned by `checked_at` for query performance.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| run_id | STRING | NO | Unique run ID (UUID short hash). Groups all rules in one execution. |
| run_type | STRING | NO | `scheduled`, `manual`, or `on_pipeline` |
| rule_id | STRING | NO | FK to rules table |
| dataset | STRING | NO | Table that was checked (denormalized) |
| rule_name | STRING | NO | Rule name (denormalized) |
| rule_type | STRING | NO | Rule type (denormalized) |
| severity | STRING | NO | Severity at time of check (denormalized) |
| owner | STRING | YES | Owner at time of check (denormalized) |
| passed | BOOLEAN | NO | TRUE = passed, FALSE = failed |
| error_message | STRING | YES | Error details if failed |
| result_value | STRING | YES | Raw SQL return value for debugging |
| execution_time_sec | DOUBLE | YES | How long the check took |
| checked_at | TIMESTAMP | NO | Execution timestamp (partition key) |

**Why denormalized?** The rules table can change (rule renamed, severity updated), but historical results should reflect the state at execution time.

---

## Table 3: `data_quality.rule_audit`

**Purpose**: Every change to a rule is tracked here — who changed what, when, and what the old/new values were.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| audit_id | STRING | NO | Unique audit entry ID |
| rule_id | STRING | NO | Which rule was changed |
| action | STRING | NO | `CREATED`, `UPDATED`, `DELETED`, `ENABLED`, `DISABLED` |
| field_changed | STRING | YES | Which column was modified (for UPDATED) |
| old_value | STRING | YES | Previous value |
| new_value | STRING | YES | New value |
| changed_by | STRING | YES | User who made the change |
| changed_at | TIMESTAMP | NO | When the change was made |

---

## Table 4: `data_quality.alert_config`

**Purpose**: Defines where DQ alerts are sent. Multiple channels can be active simultaneously. Rules can override the default channel via `notification_channel`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| config_id | STRING | NO | Unique config ID (PK). E.g. `TEAMS_DEFAULT` |
| channel_type | STRING | NO | `teams`, `email`, `slack`, `pagerduty` |
| channel_name | STRING | NO | Human-readable name |
| webhook_url | STRING | YES | For Teams/Slack webhooks |
| smtp_server | STRING | YES | For email: SMTP host |
| smtp_port | INT | YES | SMTP port (587 for TLS) |
| email_from | STRING | YES | Sender address |
| email_to | STRING | YES | Comma-separated recipients |
| min_severity | STRING | NO | Minimum severity to alert on |
| is_default | BOOLEAN | NO | Use when rule has no override |
| active | BOOLEAN | NO | Enable/disable channel |
| created_at | TIMESTAMP | NO | Created |
| updated_at | TIMESTAMP | NO | Last modified |

---

## Table 5: `data_quality.alert_log`

**Purpose**: Every alert the system sends is logged here — for debugging delivery issues and auditing notification volume.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| alert_id | STRING | NO | Unique alert ID |
| run_id | STRING | NO | Which validation run triggered this |
| config_id | STRING | NO | FK to alert_config |
| channel_type | STRING | NO | Channel used |
| num_failures | INT | NO | Total failures reported |
| num_critical | INT | YES | Critical count |
| num_warnings | INT | YES | Warning count |
| message_summary | STRING | YES | Brief alert content |
| status | STRING | NO | `sent`, `failed`, `skipped` |
| error_message | STRING | YES | Error if delivery failed |
| sent_at | TIMESTAMP | NO | Delivery timestamp |

---

## Views

| View | Purpose |
|------|---------|
| `v_latest_run` | Results from the most recent validation run |
| `v_daily_summary` | 90-day daily aggregates (pass rate, failures, runtime) |
| `v_repeat_offenders` | Most frequently failing rules in last 30 days |
| `v_active_rules` | All currently enabled rules with last run status |
