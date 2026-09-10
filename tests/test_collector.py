# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Server-side collection.

History used to be a side effect of the browser polling /api/metric, so
closing the tab stopped recording. These pin the replacement: a background
thread that polls every collecting connection on its own.
"""
import contextlib
import time

import pytest

import collectorBroker
import configBroker
import storage
from alertBroker import alerts
from historyBroker import history

TRACKED = {
    "1": {"id": "1", "desc": "Wait Classes", "sql": "select 1", "mode": "QUERY",
          "historyName": "Wait Classes"},
    "SPACE_01": {"id": "SPACE_01", "desc": "Tablespace", "sql": "select 2", "mode": "QUERY",
                 "alert": {"metric": "Usage %", "label": "TS", "warn": 80, "crit": 90}},
    "PLAIN": {"id": "PLAIN", "desc": "Untracked", "sql": "select 3", "mode": "QUERY"},
}


class StubLibrary:
    def __init__(self, queries=None):
        self._q = queries if queries is not None else TRACKED

    def get_all_queries(self):
        return list(self._q.values())


@pytest.fixture
def collector(data_dir):
    c = collectorBroker.Collector()
    c.attach_library(StubLibrary())
    alerts.clear()
    yield c
    c.stop()
    alerts.clear()


@pytest.fixture
def two_connections(data_dir):
    configBroker.save_config({"connections": [
        {"id": "a", "name": "A", "user": "ua", "password": "p", "dsn": "da", "collect": True},
        {"id": "b", "name": "B", "user": "ub", "password": "p", "dsn": "db", "collect": True},
    ], "active_index": 0})


class _Session:
    def __init__(self, user):
        self.user = user


@pytest.fixture
def results(monkeypatch):
    """Canned query results, recording which connection each call ran on."""
    seen = []

    @contextlib.contextmanager
    def fake_acquire(creds=None):
        yield _Session(creds["user"] if creds else None)

    def fake_run(conn, sql, query_id=None, binds=None, quiet=False):
        seen.append((query_id, conn.user))
        if query_id == "1":
            return [{"AAS": 2.5}]
        if query_id == "SPACE_01":
            return [{"TS": "USERS", "Usage %": 96.0}]
        return [{"X": 1}]

    monkeypatch.setattr(collectorBroker, "acquire", fake_acquire)
    monkeypatch.setattr(collectorBroker, "run_query_on", fake_run)
    return seen


@pytest.fixture
def sessions_opened(monkeypatch):
    """Count how many sessions a pass opens, and let a connection fail."""
    opened = []

    def install(fail_for=()):
        @contextlib.contextmanager
        def fake_acquire(creds=None):
            user = creds["user"] if creds else None
            opened.append(user)
            if user in fail_for:
                raise collectorBroker.ConnectionUnavailable("ORA-12541: TNS:no listener")
            yield _Session(user)
        monkeypatch.setattr(collectorBroker, "acquire", fake_acquire)
        return opened

    return install


# ------------------------------------------------------------- selection

def test_only_tagged_queries_are_polled(collector):
    ids = {q["id"] for q in collector._tracked_queries()}
    assert ids == {"1", "SPACE_01"}
    assert "PLAIN" not in ids


def test_no_library_means_no_work(data_dir):
    c = collectorBroker.Collector()
    assert c.collect_once() == 0


# ------------------------------------------------------------ collecting

def test_collect_writes_history_without_a_browser(collector, two_connections, results):
    written = collector.collect_once()
    assert written > 0
    assert history.get_available_metrics("ua@da") == {"Wait Classes": ["AAS"]}


def test_collect_covers_every_marked_connection(collector, two_connections, results):
    collector.collect_once()
    users = {u for _, u in results}
    assert users == {"ua", "ub"}
    assert history.get_available_metrics("ua@da")
    assert history.get_available_metrics("ub@db")


def test_active_connection_is_collected_even_if_unmarked(collector, data_dir, results):
    configBroker.save_config({"connections": [
        {"id": "a", "user": "ua", "password": "p", "dsn": "da", "collect": False},
    ], "active_index": 0})
    collector.collect_once()
    assert {u for _, u in results} == {"ua"}


def test_unmarked_inactive_connection_is_skipped(collector, data_dir, results):
    configBroker.save_config({"connections": [
        {"id": "a", "user": "ua", "password": "p", "dsn": "da", "collect": False},
        {"id": "b", "user": "ub", "password": "p", "dsn": "db", "collect": False},
    ], "active_index": 0})
    collector.collect_once()
    assert {u for _, u in results} == {"ua"}


def test_collect_evaluates_alerts(collector, two_connections, results):
    collector.collect_once()
    active = alerts.active("ua@da")
    assert len(active) == 1
    assert active[0]["severity"] == "CRIT"


def test_error_rows_are_neither_recorded_nor_alerted(collector, two_connections, monkeypatch):
    @contextlib.contextmanager
    def fake_acquire(creds=None):
        yield _Session(creds["user"])
    monkeypatch.setattr(collectorBroker, "acquire", fake_acquire)
    monkeypatch.setattr(collectorBroker, "run_query_on",
                        lambda *a, **k: [{"error": "ORA-942"}])
    assert collector.collect_once() == 0
    assert alerts.active() == []


def test_one_failing_query_does_not_stop_the_pass(collector, two_connections, monkeypatch):
    calls = []

    @contextlib.contextmanager
    def fake_acquire(creds=None):
        yield _Session(creds["user"])

    def flaky(conn, sql, query_id=None, binds=None, quiet=False):
        calls.append(query_id)
        if query_id == "1":
            raise RuntimeError("boom")
        return [{"TS": "USERS", "Usage %": 96.0}]

    monkeypatch.setattr(collectorBroker, "acquire", fake_acquire)
    monkeypatch.setattr(collectorBroker, "run_query_on", flaky)
    collector.collect_once()
    assert "SPACE_01" in calls
    assert alerts.active()


def test_one_session_per_database_per_cycle(collector, two_connections, results):
    """
    Not one per query: against a dead listener that was 19 connect timeouts
    every cycle instead of a single failure.
    """
    opened = []

    @contextlib.contextmanager
    def counting(creds=None):
        opened.append(creds["user"])
        yield _Session(creds["user"])

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(collectorBroker, "acquire", counting)
        collector.collect_once()
    assert sorted(opened) == ["ua", "ub"]


def test_unreachable_connection_short_circuits(collector, two_connections,
                                               sessions_opened, monkeypatch):
    """A dead database must not be retried once per tracked query."""
    ran = []
    monkeypatch.setattr(collectorBroker, "run_query_on",
                        lambda conn, sql, query_id=None, **k: ran.append(query_id) or [{"X": 1}])
    opened = sessions_opened(fail_for={"ua"})
    collector.collect_once()

    assert opened == ["ua", "ub"]          # one attempt for the dead one
    assert all(q for q in ran)             # only the healthy database ran queries
    assert len(ran) == len(collector._tracked_queries())


def test_no_connections_is_harmless(collector, data_dir, results):
    configBroker.save_config({"connections": [], "active_index": -1})
    assert collector.collect_once() == 0


# ------------------------------------------------------------- lifecycle

def test_start_and_stop(collector, two_connections, results):
    assert collector.start(interval=collectorBroker.MIN_INTERVAL) is True
    assert collector.running is True
    assert collector.start() is False        # already running
    assert collector.stop() is True
    assert collector.running is False
    assert collector.stop() is False


def test_thread_actually_collects(collector, two_connections, results):
    collector.start(interval=collectorBroker.MIN_INTERVAL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and collector.cycles == 0:
        time.sleep(0.05)
    collector.stop()
    assert collector.cycles >= 1
    assert collector.samples > 0
    assert history.get_available_metrics("ua@da")


def test_interval_is_floored(collector):
    collector.start(interval=1)
    try:
        assert collector.interval == collectorBroker.MIN_INTERVAL
    finally:
        collector.stop()


def test_status_reports_the_collecting_set(collector, two_connections, results):
    st = collector.status()
    assert st["enabled"] is False
    assert sorted(st["connections"]) == ["ua@da", "ub@db"]


# ----------------------------------------------------------- persistence

def test_state_round_trips(data_dir):
    collectorBroker.save_state(True, 300)
    assert collectorBroker.load_state() == {"enabled": True, "interval": 300}


def test_state_defaults_when_absent(data_dir):
    st = collectorBroker.load_state()
    assert st["enabled"] is False
    assert st["interval"] == collectorBroker.DEFAULT_INTERVAL


def test_state_interval_is_floored(data_dir):
    storage.write_json(collectorBroker.STATE_FILE, {"enabled": True, "interval": 1})
    assert collectorBroker.load_state()["interval"] == collectorBroker.MIN_INTERVAL
