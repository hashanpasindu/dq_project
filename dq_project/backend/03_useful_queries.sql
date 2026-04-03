-- ════════════════════════════════════════════════════════════════════════════
-- 🛡️ USEFUL QUERIES — For Databricks SQL Dashboards & Diagnostics
-- ════════════════════════════════════════════════════════════════════════════


-- ── 1. Overall health score (today) ─────────────────────────────────────
SELECT
    ROUND(SUM(CASE WHEN passed THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS health_score_pct,
    COUNT(*)                                                               AS total_checks,
    SUM(CASE WHEN passed THEN 1 ELSE 0 END)                              AS passed,
    SUM(CASE WHEN NOT passed THEN 1 ELSE 0 END)                          AS failed
FROM data_quality.v_latest_run;


-- ── 2. Failures by dataset (which tables have issues?) ──────────────────
SELECT
    dataset,
    COUNT(*)                                                    AS failed_rules,
    COLLECT_LIST(rule_name)                                    AS failed_rule_names,
    MAX(severity)                                               AS worst_severity
FROM data_quality.v_latest_run
WHERE NOT passed
GROUP BY dataset
ORDER BY failed_rules DESC;


-- ── 3. Failures by owner (who needs to act?) ────────────────────────────
SELECT
    owner,
    COUNT(*)                                                    AS failed_rules,
    SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END)    AS critical,
    SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END)     AS warnings
FROM data_quality.v_latest_run
WHERE NOT passed
GROUP BY owner
ORDER BY critical DESC, warnings DESC;


-- ── 4. Weekly pass rate trend ───────────────────────────────────────────
SELECT
    DATE_TRUNC('week', check_date)     AS week_start,
    ROUND(AVG(pass_rate_pct), 1)       AS avg_pass_rate,
    SUM(critical_fails)                AS total_critical,
    SUM(warning_fails)                 AS total_warnings
FROM data_quality.v_daily_summary
GROUP BY DATE_TRUNC('week', check_date)
ORDER BY week_start;


-- ── 5. Slowest rules (performance optimization) ────────────────────────
SELECT
    rule_id,
    rule_name,
    dataset,
    ROUND(AVG(execution_time_sec), 2)  AS avg_seconds,
    ROUND(MAX(execution_time_sec), 2)  AS max_seconds,
    COUNT(*)                            AS total_runs
FROM data_quality.results
WHERE checked_at >= current_date() - INTERVAL 30 DAYS
GROUP BY rule_id, rule_name, dataset
ORDER BY avg_seconds DESC
LIMIT 20;


-- ── 6. Rules that have never been run ───────────────────────────────────
SELECT r.rule_id, r.rule_name, r.dataset, r.severity, r.owner, r.created_at
FROM data_quality.rules r
LEFT JOIN data_quality.results res ON r.rule_id = res.rule_id
WHERE res.rule_id IS NULL AND r.active = true
ORDER BY r.created_at;


-- ── 7. Alert delivery success rate ──────────────────────────────────────
SELECT
    channel_type,
    COUNT(*)                                                      AS total_alerts,
    SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END)            AS delivered,
    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)          AS failed,
    ROUND(
        SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                              AS success_rate_pct
FROM data_quality.alert_log
WHERE sent_at >= current_date() - INTERVAL 30 DAYS
GROUP BY channel_type;


-- ── 8. Recent rule changes (audit) ──────────────────────────────────────
SELECT *
FROM data_quality.rule_audit
ORDER BY changed_at DESC
LIMIT 50;
