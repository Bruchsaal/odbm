# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Threshold evaluation and alert-state transitions."""
import pytest

from alertBroker import CRIT, OK, WARN, alerts
from logBroker import logger

CID = "scott@host/db"
RULE = {"metric": "Usage %", "label": "TABLESPACE_NAME", "warn": 80, "crit": 90}


@pytest.fixture(autouse=True)
def clean():
    alerts.clear()
    logger.clear()
    yield
    alerts.clear()


def q(**over):
    base = {"id": "SPACE_01", "desc": "Tablespace Usage", "alert": dict(RULE)}
    base.update(over)
    return base


def rows(*pairs):
    return [{"TABLESPACE_NAME": n, "Usage %": v} for n, v in pairs]


def test_below_threshold_is_silent():
    assert alerts.evaluate(q(), rows(("USERS", 10)), CID) == []
    assert alerts.worst() == OK


def test_warn_and_crit_are_classified():
    found = alerts.evaluate(q(), rows(("A", 85), ("B", 96)), CID)
    assert {a["label"]: a["severity"] for a in found} == {"A": WARN, "B": CRIT}
    assert alerts.worst() == CRIT


def test_each_row_is_evaluated_separately():
    """One tablespace at 96% must not hide behind another at 10%."""
    found = alerts.evaluate(q(), rows(("QUIET", 10), ("LOUD", 96)), CID)
    assert [a["label"] for a in found] == ["LOUD"]


def test_below_direction():
    rule = {"metric": "Free MB", "warn": 500, "crit": 100, "direction": "below"}
    found = alerts.evaluate(q(alert=rule), [{"Free MB": 50}], CID)
    assert found[0]["severity"] == CRIT
    assert alerts.evaluate(q(alert=rule), [{"Free MB": 5000}], CID) == []


def test_recovery_clears_the_alert():
    alerts.evaluate(q(), rows(("A", 96)), CID)
    assert alerts.worst() == CRIT
    alerts.evaluate(q(), rows(("A", 5)), CID)
    assert alerts.worst() == OK
    assert alerts.active() == []


def test_transitions_log_once_not_every_cycle():
    """A 30s poll must not fill the 200-entry log with the same warning."""
    for _ in range(10):
        alerts.evaluate(q(), rows(("A", 96)), CID)
    entries = [e for e in logger.get_logs() if e["source"] == "Alert"]
    assert len(entries) == 1
    assert entries[0]["level"] == "ERROR"


def test_escalation_is_logged():
    alerts.evaluate(q(), rows(("A", 85)), CID)
    alerts.evaluate(q(), rows(("A", 96)), CID)
    levels = [e["level"] for e in logger.get_logs() if e["source"] == "Alert"]
    assert levels == ["ERROR", "WARN"]      # newest first


def test_recovery_is_logged():
    alerts.evaluate(q(), rows(("A", 96)), CID)
    logger.clear()
    alerts.evaluate(q(), rows(("A", 1)), CID)
    entries = [e for e in logger.get_logs() if e["source"] == "Alert"]
    assert len(entries) == 1 and entries[0]["level"] == "INFO"


def test_queries_without_a_rule_are_ignored():
    assert alerts.evaluate({"id": "1", "desc": "x"}, rows(("A", 99)), CID) == []


def test_error_rows_do_not_alert():
    assert alerts.evaluate(q(), [{"error": "ORA-942"}], CID) == []


def test_non_numeric_metric_is_skipped():
    assert alerts.evaluate(q(), [{"TABLESPACE_NAME": "A", "Usage %": "n/a"}], CID) == []


def test_missing_metric_column_is_skipped():
    assert alerts.evaluate(q(), [{"TABLESPACE_NAME": "A", "Other": 99}], CID) == []


def test_alerts_are_scoped_per_connection():
    other = "app@other/db"
    alerts.evaluate(q(), rows(("A", 96)), CID)
    alerts.evaluate(q(), rows(("B", 96)), other)
    assert len(alerts.active(CID)) == 1
    assert len(alerts.active(other)) == 1
    assert len(alerts.active()) == 2
    alerts.clear(CID)
    assert alerts.active(CID) == []
    assert len(alerts.active(other)) == 1


def test_active_is_sorted_worst_first():
    alerts.evaluate(q(), rows(("A", 85), ("B", 99), ("C", 92)), CID)
    sev = [a["severity"] for a in alerts.active()]
    assert sev == sorted(sev, key=lambda s: {CRIT: 0, WARN: 1}[s])
    assert alerts.active()[0]["label"] == "B"
