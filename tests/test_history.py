# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import historyBroker
from historyBroker import history

CID = "scott@host:1521/FREE"


def test_untagged_query_records_nothing(data_dir):
    """History is opt-in per query; an empty historyName means skip."""
    assert history.record(None, [{"AAS": 1}], CID) == 0
    assert history.record("", [{"AAS": 1}], CID) == 0
    assert history.get_available_metrics(CID) == {}


def test_tagged_query_records_numeric_columns(data_dir):
    assert history.record("Wait Classes", [{"CPU": 1.5, "IO": 2.0}], CID) == 2
    assert history.get_available_metrics(CID) == {"Wait Classes": ["CPU", "IO"]}


def test_non_numeric_and_null_columns_are_skipped(data_dir):
    written = history.record("S", [{"NAME": "text", "NOTHING": None, "AAS": 3}], CID)
    assert written == 1
    assert history.get_available_metrics(CID) == {"S": ["AAS"]}


def test_numeric_strings_are_accepted(data_dir):
    assert history.record("S", [{"PCT": "42.5"}], CID) == 1
    assert history.get_series("S", "PCT", CID)["PCT"][0]["y"] == 42.5


def test_error_rows_are_not_recorded(data_dir):
    assert history.record("S", [{"error": "ORA-942"}], CID) == 0


def test_empty_rows_are_not_recorded(data_dir):
    assert history.record("S", [], CID) == 0
    assert history.record("S", None, CID) == 0


def test_default_connection_is_not_recorded(data_dir):
    assert history.record("S", [{"AAS": 1}], "default") == 0
    assert history.record("S", [{"AAS": 1}], "") == 0


def test_series_are_returned_oldest_first(data_dir):
    for i in range(5):
        history.record("S", [{"AAS": i}], CID)
    values = [p["y"] for p in history.get_series("S", "AAS", CID)["AAS"]]
    assert values == [0, 1, 2, 3, 4]


def test_series_without_metric_returns_every_metric(data_dir):
    history.record("S", [{"A": 1, "B": 2}], CID)
    series = history.get_series("S", None, CID)
    assert set(series) == {"A", "B"}


def test_retention_trims_old_points(data_dir, monkeypatch):
    """MAX_HISTORY_POINTS is a real cap now, not just a SELECT limit."""
    monkeypatch.setattr(historyBroker, "MAX_HISTORY_POINTS", 10)
    for i in range(40):
        history.record("S", [{"AAS": i}], CID)
    history.prune_now(CID)

    values = [p["y"] for p in history.get_series("S", "AAS", CID)["AAS"]]
    assert len(values) == 10
    assert values == list(range(30, 40))  # newest kept


def test_retention_is_per_series(data_dir, monkeypatch):
    monkeypatch.setattr(historyBroker, "MAX_HISTORY_POINTS", 5)
    for i in range(10):
        history.record("S", [{"A": i, "B": i}], CID)
    history.prune_now(CID)
    series = history.get_series("S", None, CID)
    assert len(series["A"]) == 5
    assert len(series["B"]) == 5


def test_automatic_prune_fires_on_schedule(data_dir, monkeypatch):
    monkeypatch.setattr(historyBroker, "MAX_HISTORY_POINTS", 5)
    monkeypatch.setattr(historyBroker, "PRUNE_EVERY", 10)
    history._writes_since_prune = 0
    for i in range(10):
        history.record("S", [{"A": i}], CID)
    # No explicit prune_now(): the sweep runs itself.
    assert len(history.get_series("S", "A", CID)["A"]) == 5


def test_each_connection_gets_its_own_store(data_dir):
    other = "app@other:1521/PROD"
    history.record("S", [{"A": 1}], CID)
    history.record("S", [{"B": 2}], other)
    assert history.get_available_metrics(CID) == {"S": ["A"]}
    assert history.get_available_metrics(other) == {"S": ["B"]}


def test_clear_removes_only_the_given_connection(data_dir):
    other = "app@other:1521/PROD"
    history.record("S", [{"A": 1}], CID)
    history.record("S", [{"B": 2}], other)
    assert history.clear(CID) is True
    assert history.get_available_metrics(CID) == {}
    assert history.get_available_metrics(other) == {"S": ["B"]}


def test_clear_is_safe_when_nothing_exists(data_dir):
    assert history.clear(CID) is False
    assert history.clear(None) is False


def test_reads_on_missing_store_are_empty(data_dir):
    assert history.get_available_metrics(CID) == {}
    assert history.get_series("S", "A", CID) == {}


def test_db_filename_is_sanitised(data_dir):
    path = history._get_db_path("scott@host:1521/FREE")
    assert "/FREE" not in path.replace(str(data_dir), "")
    assert path.endswith("history_scott_host_1521_FREE.db")
