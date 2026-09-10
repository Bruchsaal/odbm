# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import re
import sqlite3
import threading
from datetime import datetime

import storage
from logBroker import logger

# Rows kept per (source, metric) series. Older points are pruned so the
# store stays bounded instead of growing for the life of the install.
MAX_HISTORY_POINTS = 20000
PRUNE_EVERY = 200          # record() calls between retention sweeps


class HistoryBroker:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            instance = super(HistoryBroker, cls).__new__(cls)
            instance._writes_since_prune = 0
            instance._lock = threading.Lock()
            cls._instance = instance
        return cls._instance

    def _get_db_path(self, connection_id):
        safe_id = re.sub(r'[^a-zA-Z0-9]', '_', connection_id)
        return storage.data_file(f"history_{safe_id}.db")

    def _get_conn(self, connection_id):
        db_path = self._get_db_path(connection_id)
        initialize = not os.path.exists(db_path)
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=20)
        if initialize:
            self._init_schema(conn)
        return conn

    def _init_schema(self, conn):
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    metric TEXT,
                    timestamp TEXT,
                    value REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source_metric ON history(source, metric)")
        # WAL keeps readers from blocking the collector mid-write.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass

    def record(self, source_name, rows, connection_id):
        """
        Store the numeric columns of the first row under source_name.
        A falsy source_name means the query is not marked for history, so
        nothing is written.
        """
        if not source_name:
            return 0
        if not rows or not isinstance(rows[0], dict) or "error" in rows[0]:
            return 0
        if not connection_id or connection_id == "default":
            return 0

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_to_insert = []
        for col, val in rows[0].items():
            if val is None or isinstance(val, bool):
                continue
            try:
                numeric_val = float(val)
            except (ValueError, TypeError):
                continue  # non-numeric column, not a metric
            data_to_insert.append((source_name, str(col).strip(), timestamp, numeric_val))

        if not data_to_insert:
            return 0

        conn = self._get_conn(connection_id)
        try:
            with conn:
                conn.executemany(
                    "INSERT INTO history (source, metric, timestamp, value) VALUES (?, ?, ?, ?)",
                    data_to_insert,
                )
            with self._lock:
                self._writes_since_prune += 1
                due = self._writes_since_prune >= PRUNE_EVERY
                if due:
                    self._writes_since_prune = 0
            if due:
                self._prune(conn)
            return len(data_to_insert)
        except sqlite3.Error as e:
            logger.error("History", f"Write failed for {connection_id}", str(e))
            return 0
        finally:
            conn.close()

    def _prune(self, conn):
        """Trim every series down to MAX_HISTORY_POINTS most recent rows."""
        try:
            with conn:
                conn.execute(
                    """
                    DELETE FROM history WHERE id IN (
                        SELECT id FROM (
                            SELECT id, ROW_NUMBER() OVER (
                                PARTITION BY source, metric ORDER BY id DESC
                            ) AS rn
                            FROM history
                        ) WHERE rn > ?
                    )
                    """,
                    (MAX_HISTORY_POINTS,),
                )
        except sqlite3.Error as e:
            logger.warn("History", "Retention sweep failed", str(e))

    def prune_now(self, connection_id):
        """Force a retention sweep. Used by tests and maintenance."""
        if not os.path.exists(self._get_db_path(connection_id)):
            return
        conn = self._get_conn(connection_id)
        try:
            self._prune(conn)
        finally:
            conn.close()

    def get_available_metrics(self, connection_id):
        if not os.path.exists(self._get_db_path(connection_id)):
            return {}
        conn = self._get_conn(connection_id)
        try:
            cursor = conn.execute("SELECT DISTINCT source, metric FROM history ORDER BY source, metric")
            options = {}
            for source, metric in cursor.fetchall():
                options.setdefault(source, []).append(metric)
            return options
        except sqlite3.Error as e:
            logger.error("History", "Could not list metrics", str(e))
            return {}
        finally:
            conn.close()

    def get_series(self, source, metric, connection_id):
        if not os.path.exists(self._get_db_path(connection_id)):
            return {}
        conn = self._get_conn(connection_id)
        try:
            if metric:
                cursor = conn.execute(
                    "SELECT timestamp, value FROM history WHERE source = ? AND metric = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (source, metric, MAX_HISTORY_POINTS),
                )
                data = [{"t": r[0], "y": r[1]} for r in cursor.fetchall()]
                return {metric: data[::-1]}

            cursor = conn.execute(
                "SELECT metric, timestamp, value FROM history WHERE source = ? "
                "ORDER BY id DESC LIMIT ?",
                (source, MAX_HISTORY_POINTS * 10),
            )
            result = {}
            for m_name, t, v in cursor.fetchall():
                bucket = result.setdefault(m_name, [])
                if len(bucket) < MAX_HISTORY_POINTS:
                    bucket.append({"t": t, "y": v})
            for k in result:
                result[k].reverse()
            return result
        except sqlite3.Error as e:
            logger.error("History", "Could not read series", str(e))
            return {}
        finally:
            conn.close()

    def clear(self, connection_id=None):
        if not connection_id:
            return False
        db_path = self._get_db_path(connection_id)
        if not os.path.exists(db_path):
            return False
        try:
            os.remove(db_path)
            return True
        except OSError as e:
            logger.error("History", "Could not clear history", str(e))
            return False


history = HistoryBroker()
