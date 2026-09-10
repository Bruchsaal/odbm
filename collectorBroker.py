# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Server-side metric collection.

History used to be a side effect of the browser polling /api/metric, so
closing the tab silently stopped recording and left gaps in the Graphs tab.
This runs the tagged queries on a background thread instead, independent of
any browser, across every connection marked for collection.

A plain thread rather than an asyncio task: the Oracle driver is synchronous,
so awaiting it would block the event loop serving the UI.
"""
import threading
import time

import configBroker
import storage
from alertBroker import alerts
from dbBroker import ConnectionUnavailable, acquire, run_query_on
from historyBroker import history
from logBroker import logger

STATE_FILE = "collector.json"
DEFAULT_INTERVAL = 60
MIN_INTERVAL = 10


def load_state():
    data = storage.read_json(STATE_FILE, default=None) or {}
    return {
        "enabled": bool(data.get("enabled", False)),
        "interval": max(MIN_INTERVAL, int(data.get("interval", DEFAULT_INTERVAL) or DEFAULT_INTERVAL)),
    }


def save_state(enabled, interval):
    storage.write_json(STATE_FILE, {"enabled": bool(enabled),
                                    "interval": max(MIN_INTERVAL, int(interval))})


class Collector:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lib = None
        self.interval = DEFAULT_INTERVAL
        self.last_run = None
        self.last_error = None
        self.cycles = 0
        self.samples = 0

    # ---------------------------------------------------------- lifecycle

    def attach_library(self, lib):
        self._lib = lib

    def start(self, interval=None):
        with self._lock:
            if interval:
                self.interval = max(MIN_INTERVAL, int(interval))
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="odbm-collector", daemon=True)
            self._thread.start()
            logger.info("Collector", f"Background collection started ({self.interval}s)")
            return True

    def stop(self, timeout=5):
        with self._lock:
            thread = self._thread
            self._thread = None
        if not thread:
            return False
        self._stop.set()
        thread.join(timeout=timeout)
        logger.info("Collector", "Background collection stopped")
        return True

    @property
    def running(self):
        t = self._thread
        return bool(t and t.is_alive())

    def status(self):
        return {
            "enabled": self.running,
            "interval": self.interval,
            "last_run": self.last_run,
            "last_error": self.last_error,
            "cycles": self.cycles,
            "samples": self.samples,
            "connections": [configBroker.connection_id(c)
                            for c in configBroker.get_collecting_connections()],
        }

    # ------------------------------------------------------------- work

    def _tracked_queries(self):
        """Queries that need polling: trended, alerting, or both."""
        if not self._lib:
            return []
        return [q for q in self._lib.get_all_queries()
                if q.get("historyName") or isinstance(q.get("alert"), dict)]

    def collect_once(self):
        """One full pass over every collecting connection. Returns samples written."""
        queries = self._tracked_queries()
        if not queries:
            return 0

        written = 0
        for creds in configBroker.get_collecting_connections():
            conn_id = configBroker.connection_id(creds)
            try:
                # One session per database per cycle. If the listener is down
                # this fails once, rather than timing out per tracked query.
                with acquire(creds) as conn:
                    for query in queries:
                        if self._stop.is_set():
                            return written
                        try:
                            rows = run_query_on(conn, query["sql"], query["id"], quiet=True)
                        except Exception as e:   # never let one query kill the pass
                            logger.error("Collector", f"Query {query['id']} raised", str(e))
                            continue
                        if not rows or not isinstance(rows[0], dict) or "error" in rows[0]:
                            continue
                        if query.get("historyName"):
                            written += history.record(query["historyName"], rows, conn_id)
                        alerts.evaluate(query, rows, conn_id)
            except ConnectionUnavailable:
                continue          # already logged once by dbBroker
            except Exception as e:
                logger.error("Collector", f"Collection failed for {conn_id}", str(e))
        return written

    def _loop(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.samples += self.collect_once()
                self.cycles += 1
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                logger.error("Collector", "Collection cycle failed", str(e))
            self.last_run = time.strftime("%H:%M:%S")

            # Interruptible sleep, minus however long the pass took.
            elapsed = time.monotonic() - started
            self._stop.wait(max(1.0, self.interval - elapsed))


collector = Collector()
