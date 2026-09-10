# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import itertools
import os
import threading
from collections import deque
from datetime import datetime

# Verbose path/boot tracing is opt-in; it used to be printed on every request.
DEBUG = os.environ.get("ODBM_DEBUG", "").lower() in ("1", "true", "yes")


class LogBroker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LogBroker, cls).__new__(cls)
            # keep last 200 in memory. singleton setup.
            cls._instance.logs = deque(maxlen=200)
            cls._instance._ids = itertools.count(1)
        return cls._instance

    def log(self, level, source, message, details=""):
        level = level.upper()
        if level == "DEBUG" and not DEBUG:
            return

        with self._lock:
            entry_id = next(self._ids)

        entry = {
            "id": entry_id,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "source": source,
            "message": message,
            "details": str(details) if details else "",
        }

        # console output for debugging server side
        if level == "ERROR":
            print(f"[ERROR] [{source}] {message}")
        elif level == "WARN":
            print(f"[WARN]  [{source}] {message}")
        elif level == "DEBUG":
            print(f"[DEBUG] [{source}] {message}")
        else:
            print(f"[INFO]  [{source}] {message}")

        self.logs.appendleft(entry)

    def debug(self, source, message, details=""):
        self.log("DEBUG", source, message, details)

    def info(self, source, message, details=""):
        self.log("INFO", source, message, details)

    def warn(self, source, message, details=""):
        self.log("WARN", source, message, details)

    def error(self, source, message, details=""):
        self.log("ERROR", source, message, details)

    def get_logs(self):
        # dump the whole list
        return list(self.logs)

    def clear(self):
        self.logs.clear()


logger = LogBroker()
