"""
templates.py — DQ Rule Templates
Each template defines fields and a SQL builder function.
"""

TEMPLATES = {
    "⏰ Freshness": {
        "key": "freshness",
        "desc": "Check if data arrived within N days",
        "fields": ["date_column", "interval_days"],
        "sql": lambda t, c, n: f"SELECT MAX({c}) >= current_date() - INTERVAL {n} DAY FROM {t}",
    },
    "📊 Row Count": {
        "key": "row_count",
        "desc": "Check if today's partition has enough rows",
        "fields": ["date_column", "min_rows"],
        "sql": lambda t, c, n: (
            f"SELECT COUNT(*) >= {n} FROM {t} WHERE {c} = current_date()" if int(n or 0) > 0
            else f"SELECT COUNT(*) > 0 FROM {t} WHERE {c} = current_date()"
        ),
    },
    "🚫 Null Check": {
        "key": "null_check",
        "desc": "Ensure a column has no NULLs",
        "fields": ["target_column"],
        "sql": lambda t, c: f"SELECT COUNT(*) = 0 FROM {t} WHERE {c} IS NULL",
    },
    "🔑 Uniqueness": {
        "key": "uniqueness",
        "desc": "Ensure no duplicate values in a column",
        "fields": ["target_column"],
        "sql": lambda t, c: f"SELECT COUNT(*) = COUNT(DISTINCT {c}) FROM {t}",
    },
    "📐 Value Range": {
        "key": "business_logic",
        "desc": "Ensure values fall within min–max bounds",
        "fields": ["target_column", "min_val", "max_val"],
        "sql": lambda t, c, mn, mx: f"SELECT COUNT(*) = 0 FROM {t} WHERE {c} < {mn} OR {c} > {mx}",
    },
    "📋 Allowed Values": {
        "key": "business_logic",
        "desc": "Ensure values are from a known set",
        "fields": ["target_column", "allowed_values"],
        "sql": lambda t, c, v: (
            f"SELECT COUNT(*) = 0 FROM {t} WHERE {c} NOT IN "
            f"({','.join(repr(x.strip()) for x in v.split(','))})"
        ),
    },
    "🔗 Referential Integrity": {
        "key": "referential",
        "desc": "Ensure foreign keys match a parent table",
        "fields": ["target_column", "parent_table", "parent_column"],
        "sql": lambda t, c, pt, pc: (
            f"SELECT COUNT(*) = 0 FROM {t} a LEFT JOIN {pt} b ON a.{c} = b.{pc} WHERE b.{pc} IS NULL"
        ),
    },
    "✏️ Custom SQL": {
        "key": "custom",
        "desc": "Write your own SQL (must return TRUE or FALSE)",
        "fields": ["custom_sql"],
        "sql": lambda _t, s: s,
    },
}
