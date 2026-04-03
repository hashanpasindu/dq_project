"""
config.py — Local configuration management
Saves Databricks connection details to ~/.dq_config.json
"""

import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".dq_config.json"


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def clear_config():
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
