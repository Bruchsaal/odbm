# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import storage
from logBroker import logger

SETTINGS_FILE = "settings.json"

# default mapping: widget keys -> query ids
DEFAULT_SETTINGS = {
    "wait_classes": "1",
    "owner_space": "9",
    "latency": "2",
    "used_space": "8",
    "aas": "27",
    "fra": "13",
    "sys_info": "96",
    "avail_status": "AVAIL_01",
    "avail_invalid": "AVAIL_02",
    "perf_active": "PERF_01",
    "perf_blocking": "PERF_02",
    "space_visual": "SPACE_01",
    "detailed_storage": "3",
    "asm_usage": "ASM_01",
    "sga": "4",
    "pga": "5",
    "opti": "6",
    "cursor": "7",
    "datafiles": "16",
    "sql_summary": "10",
    "sql_single": "11",
    "rman_backups": "17",
    "rman_errors": "18",
    "rman_list": "19",
    "users": "26",
    "profiles": "28",
    "advisor": "29",
    "alerts": "30",
    "dp_jobs": "21",
    "dp_sessions": "22",
    "resumable": "24",
    "longops": "23",
    "sessions_tab": "20",
}


def load_widget_settings():
    data = storage.read_json(SETTINGS_FILE, default=None)
    if not isinstance(data, dict):
        if data is not None:
            logger.warn("Widgets", "settings.json malformed, restoring defaults")
        save_widget_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    # merge with defaults to catch new keys
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in data.items() if isinstance(v, str)})
    return merged


def save_widget_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("widget settings must be an object")
    try:
        storage.write_json(SETTINGS_FILE, settings)
    except OSError as e:
        logger.error("Widgets", "Failed to save settings", str(e))
        raise
