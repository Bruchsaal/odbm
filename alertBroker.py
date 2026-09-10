# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Threshold evaluation for query results.

A rule lives on the query itself, alongside historyName, so it is editable in
the Library tab rather than hardcoded here:

    "alert": {
        "metric": "Usage %",            # numeric column to test
        "label": "TABLESPACE_NAME",     # optional column naming the offender
        "warn": 80,
        "crit": 90,
        "direction": "above"            # or "below"
    }

Multi-row results are evaluated row by row and reported per label, so one
tablespace crossing 90% does not hide behind another sitting at 10%.
"""
import threading

from logBroker import logger

OK, WARN, CRIT = "OK", "WARN", "CRIT"
_RANK = {OK: 0, WARN: 1, CRIT: 2}


class AlertBroker:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._state = {}          # key -> alert dict
            inst._lock = threading.Lock()
            cls._instance = inst
        return cls._instance

    @staticmethod
    def _severity(value, rule):
        warn, crit = rule.get("warn"), rule.get("crit")
        below = str(rule.get("direction", "above")).lower() == "below"
        if below:
            if crit is not None and value <= crit:
                return CRIT
            if warn is not None and value <= warn:
                return WARN
        else:
            if crit is not None and value >= crit:
                return CRIT
            if warn is not None and value >= warn:
                return WARN
        return OK

    def evaluate(self, query, rows, connection_id):
        """
        Apply a query's alert rule to its result set.

        Returns the list of non-OK alerts. Transitions are logged once, so a
        five-second poll does not fill the log with the same warning.
        """
        rule = (query or {}).get("alert")
        if not isinstance(rule, dict) or not rule.get("metric"):
            return []
        if not rows or not isinstance(rows[0], dict) or "error" in rows[0]:
            return []

        metric = rule["metric"]
        label_col = rule.get("label")
        query_id = str(query.get("id", "?"))
        title = query.get("desc", query_id)
        found = []

        for row in rows:
            if metric not in row:
                continue
            try:
                value = float(row[metric])
            except (TypeError, ValueError):
                continue

            label = str(row.get(label_col)) if label_col and row.get(label_col) is not None else None
            key = f"{connection_id}|{query_id}|{label or metric}"
            severity = self._severity(value, rule)
            alert = {
                "key": key,
                "connection": connection_id,
                "query_id": query_id,
                "title": title,
                "metric": metric,
                "label": label,
                "value": round(value, 2),
                "severity": severity,
                "warn": rule.get("warn"),
                "crit": rule.get("crit"),
                "direction": rule.get("direction", "above"),
            }

            with self._lock:
                previous = self._state.get(key, {}).get("severity", OK)
                if severity == OK:
                    self._state.pop(key, None)
                else:
                    self._state[key] = alert

            if severity != previous:
                self._log_transition(alert, previous)
            if severity != OK:
                found.append(alert)

        return found

    def _log_transition(self, alert, previous):
        where = f" [{alert['label']}]" if alert["label"] else ""
        arrow = "above" if alert["direction"] == "above" else "below"
        threshold = alert["crit"] if alert["severity"] == CRIT else alert["warn"]
        detail = (f"{alert['title']}{where} on {alert['connection']}: "
                  f"{alert['metric']} = {alert['value']} ({arrow} {threshold})")
        if alert["severity"] == CRIT:
            logger.error("Alert", f"CRITICAL: {alert['metric']}{where} = {alert['value']}", detail)
        elif alert["severity"] == WARN:
            logger.warn("Alert", f"Warning: {alert['metric']}{where} = {alert['value']}", detail)
        else:
            logger.info("Alert", f"Recovered: {alert['metric']}{where} = {alert['value']}", detail)

    def active(self, connection_id=None):
        """Current non-OK alerts, worst first."""
        with self._lock:
            items = list(self._state.values())
        if connection_id:
            items = [a for a in items if a["connection"] == connection_id]
        return sorted(items, key=lambda a: (-_RANK[a["severity"]], -a["value"]))

    def worst(self, connection_id=None):
        items = self.active(connection_id)
        return items[0]["severity"] if items else OK

    def clear(self, connection_id=None):
        with self._lock:
            if connection_id is None:
                self._state.clear()
            else:
                for k in [k for k, a in self._state.items()
                          if a["connection"] == connection_id]:
                    del self._state[k]


alerts = AlertBroker()
