# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

import dbBroker
from conftest import FakeConnection, FakePool, oracle_error


@pytest.fixture
def direct(monkeypatch):
    """Force direct connections and hand back the connections that were made."""
    made = []

    def fake_connect(**kwargs):
        conn = FakeConnection(**fake_connect.conn_kwargs)
        conn.connect_kwargs = kwargs
        made.append(conn)
        return conn

    fake_connect.conn_kwargs = {}
    monkeypatch.setattr(dbBroker.oracledb, "connect", fake_connect)
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
    return made, fake_connect


def test_query_returns_dict_rows(active_conn, direct):
    made, cfg = direct
    cfg.conn_kwargs = {"description": [("A",), ("B",)], "rows": [(1, "x"), (2, "y")]}
    assert dbBroker.run_query_json("select a,b from t") == [
        {"A": 1, "B": "x"}, {"A": 2, "B": "y"},
    ]


def test_session_is_closed_after_success(active_conn, direct):
    made, cfg = direct
    cfg.conn_kwargs = {"description": [("A",)], "rows": [(1,)]}
    dbBroker.run_query_json("select 1 from dual")
    assert made[0].closed is True


def test_session_is_closed_after_oracle_error(active_conn, direct):
    made, cfg = direct
    cfg.conn_kwargs = {"raise_on_execute": oracle_error(942, "table or view does not exist")}
    result = dbBroker.run_query_json("select * from nope")
    assert result[0]["error"].startswith("ORA-942")
    assert made[0].closed is True


def test_session_is_closed_after_unexpected_error(active_conn, direct):
    """Regression: the generic handler used to skip conn.close()."""
    made, cfg = direct
    cfg.conn_kwargs = {"raise_on_execute": RuntimeError("boom")}
    result = dbBroker.run_query_json("select 1 from dual")
    assert result == [{"error": "boom"}]
    assert made[0].closed is True


def test_no_active_connection_is_reported_to_the_caller(data_dir, monkeypatch):
    """Still surfaced in the result payload — only the log level softened."""
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
    result = dbBroker.run_query_json("select 1 from dual")
    assert "error" in result[0]
    assert "No database connection configured" in result[0]["error"]


def test_connect_failure_is_reported_not_raised(active_conn, monkeypatch):
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)

    def boom(**kwargs):
        raise oracle_error(1017, "invalid username/password")

    monkeypatch.setattr(dbBroker.oracledb, "connect", boom)
    result = dbBroker.run_query_json("select 1 from dual")
    assert result[0]["error"].startswith("ORA-1017")


def test_statement_without_result_set_reports_success(active_conn, direct):
    made, cfg = direct
    cfg.conn_kwargs = {"description": None, "rows": []}
    assert dbBroker.run_query_json("begin null; end;") == [{"status": "Success"}]


def test_binds_reach_the_cursor(active_conn, direct):
    made, cfg = direct
    cfg.conn_kwargs = {"description": [("SQL_TEXT",)], "rows": [("x",)]}
    dbBroker.run_query_json("select sql_text from v$sqltext where sql_id=:sql_id",
                            binds={"sql_id": "abc123"})
    sql, binds = made[0].executed[0]
    assert binds == {"sql_id": "abc123"}


def test_command_commits_and_closes(active_conn, direct):
    made, cfg = direct
    cfg.conn_kwargs = {"description": None, "rows": [(1,), (2,)]}
    result = dbBroker.run_command_json("delete from t")
    assert result[0]["status"] == "Success"
    assert result[0]["rows_affected"] == 2
    assert made[0].committed is True
    assert made[0].closed is True


def test_command_does_not_commit_on_error(active_conn, direct):
    made, cfg = direct
    cfg.conn_kwargs = {"raise_on_execute": oracle_error(1031, "insufficient privileges")}
    result = dbBroker.run_command_json("drop table t")
    assert result[0]["error"].startswith("ORA-1031")
    assert made[0].committed is False
    assert made[0].closed is True


# ------------------------------------------------------------------- pooling

def test_pool_is_created_once_and_reused(active_conn, monkeypatch):
    calls = []

    def fake_create_pool(**kwargs):
        calls.append(kwargs)
        return FakePool(lambda: FakeConnection(description=[("A",)], rows=[(1,)]))

    monkeypatch.setattr(dbBroker.oracledb, "create_pool", fake_create_pool)
    for _ in range(3):
        dbBroker.run_query_json("select 1 from dual")
    assert len(calls) == 1


def test_pooled_sessions_are_returned_to_the_pool(active_conn, monkeypatch):
    pool = FakePool(lambda: FakeConnection(description=[("A",)], rows=[(1,)]))
    monkeypatch.setattr(dbBroker.oracledb, "create_pool", lambda **kw: pool)
    for _ in range(3):
        dbBroker.run_query_json("select 1 from dual")
    assert pool.acquired == 3
    assert pool.released == 3


def test_pool_creation_failure_falls_back_to_direct(active_conn, monkeypatch):
    def boom(**kwargs):
        raise oracle_error(12541, "TNS:no listener")

    made = []

    def fake_connect(**kwargs):
        conn = FakeConnection(description=[("A",)], rows=[(1,)])
        made.append(conn)
        return conn

    monkeypatch.setattr(dbBroker.oracledb, "create_pool", boom)
    monkeypatch.setattr(dbBroker.oracledb, "connect", fake_connect)

    assert dbBroker.run_query_json("select 1 from dual") == [{"A": 1}]
    assert made[0].closed is True


def test_pool_failure_is_not_retried_immediately(active_conn, monkeypatch):
    attempts = []

    def boom(**kwargs):
        attempts.append(1)
        raise oracle_error(12541, "TNS:no listener")

    monkeypatch.setattr(dbBroker.oracledb, "create_pool", boom)
    monkeypatch.setattr(dbBroker.oracledb, "connect",
                        lambda **kw: FakeConnection(description=[("A",)], rows=[(1,)]))
    for _ in range(5):
        dbBroker.run_query_json("select 1 from dual")
    assert len(attempts) == 1  # backoff prevents a storm of slow retries


def test_sysdba_connections_are_not_pooled(data_dir, monkeypatch):
    import configBroker
    configBroker.save_config({"connections": [{
        "id": "s", "name": "S", "user": "sys", "password": "pw",
        "dsn": "d", "sysdba": True}], "active_index": 0})

    monkeypatch.setattr(dbBroker.oracledb, "create_pool",
                        lambda **kw: pytest.fail("SYSDBA must not be pooled"))
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection(description=[("A",)], rows=[(1,)])

    monkeypatch.setattr(dbBroker.oracledb, "connect", fake_connect)
    dbBroker.run_query_json("select 1 from dual")
    assert captured["mode"] == dbBroker.oracledb.SYSDBA


def test_changing_password_retires_the_old_pool(active_conn, monkeypatch):
    import configBroker
    pools = []

    def fake_create_pool(**kwargs):
        pools.append(kwargs)
        return FakePool(lambda: FakeConnection(description=[("A",)], rows=[(1,)]))

    monkeypatch.setattr(dbBroker.oracledb, "create_pool", fake_create_pool)
    dbBroker.run_query_json("select 1 from dual")

    configBroker.save_config({"connections": [{
        "id": "conn-a", "name": "Test", "user": "scott",
        "password": "new-password", "dsn": "host:1521/FREE"}], "active_index": 0})
    dbBroker.run_query_json("select 1 from dual")

    assert len(pools) == 2
    assert pools[1]["password"] == "new-password"


def test_oracle_message_handles_driver_errors_without_a_code():
    """DPY-* driver failures have no .args[0].code; formatting must not raise."""
    import oracledb
    assert dbBroker._oracle_message(oracledb.DatabaseError("plain string")) == "plain string"
    assert dbBroker._oracle_message(oracledb.DatabaseError()) == ""


def test_connection_test_endpoint_survives_a_codeless_error(client, monkeypatch):
    import oracledb
    def boom(**kwargs):
        raise oracledb.DatabaseError("DPY-6005: cannot connect to database")
    monkeypatch.setattr(oracledb, "connect", boom)
    body = client.post("/api/config/test", json={
        "user": "u", "password": "p", "dsn": "d"}).json()
    assert body["status"] == "error"
    assert "DPY-6005" in body["message"]


def _levels(source="DB"):
    from logBroker import logger
    return [e["level"] for e in logger.get_logs() if e["source"] == source]


def test_unconfigured_connection_warns_rather_than_errors(data_dir, monkeypatch):
    """Nothing configured yet is a setup state, not a failure."""
    from logBroker import logger
    monkeypatch.setattr(dbBroker, "_warned_unconfigured", False)
    logger.clear()
    dbBroker.run_query_json("select 1 from dual")
    assert "WARN" in _levels()
    assert "ERROR" not in _levels()


def test_unconfigured_warning_is_not_repeated(data_dir, monkeypatch):
    """Every polled widget hits this path; the log must not fill with copies."""
    from logBroker import logger
    monkeypatch.setattr(dbBroker, "_warned_unconfigured", False)
    logger.clear()
    for _ in range(20):
        dbBroker.run_query_json("select 1 from dual")
    assert _levels().count("WARN") == 1


def test_a_real_connection_failure_is_still_an_error(active_conn, monkeypatch):
    """A configured connection that will not open is a genuine error."""
    from logBroker import logger
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)

    def boom(**kwargs):
        raise oracle_error(1017, "invalid username/password")

    monkeypatch.setattr(dbBroker.oracledb, "connect", boom)
    logger.clear()
    dbBroker.run_query_json("select 1 from dual")
    assert "ERROR" in _levels()


def test_warning_rearms_after_a_connection_is_added(data_dir, monkeypatch):
    """Removing the connection again should warn once more."""
    from logBroker import logger
    import configBroker
    monkeypatch.setattr(dbBroker, "_warned_unconfigured", False)
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
    monkeypatch.setattr(dbBroker.oracledb, "connect",
                        lambda **kw: FakeConnection(description=[("A",)], rows=[(1,)]))

    logger.clear()
    dbBroker.run_query_json("select 1 from dual")          # unconfigured -> warn
    assert _levels().count("WARN") == 1

    configBroker.save_config({"connections": [{
        "id": "c", "user": "u", "password": "p", "dsn": "d"}], "active_index": 0})
    dbBroker.run_query_json("select 1 from dual")          # now configured

    configBroker.save_config({"connections": [], "active_index": -1})
    logger.clear()
    dbBroker.run_query_json("select 1 from dual")          # unconfigured again
    assert _levels().count("WARN") == 1


def test_repeated_connect_failures_log_once(active_conn, monkeypatch):
    """
    The collector retries every cycle; without de-duplication a dead listener
    fills the 200-entry log within minutes and evicts everything useful.
    """
    from logBroker import logger
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
    monkeypatch.setattr(dbBroker, "_connect_failures", {})

    def boom(**kwargs):
        raise oracle_error(12541, "TNS:no listener")

    monkeypatch.setattr(dbBroker.oracledb, "connect", boom)
    logger.clear()
    for _ in range(20):
        dbBroker.run_query_json("select 1 from dual")
    assert _levels().count("ERROR") == 1


def test_recovery_after_a_connect_failure_is_logged(active_conn, monkeypatch):
    from logBroker import logger
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
    monkeypatch.setattr(dbBroker, "_connect_failures", {})

    state = {"up": False}

    def maybe(**kwargs):
        if not state["up"]:
            raise oracle_error(12541, "TNS:no listener")
        return FakeConnection(description=[("A",)], rows=[(1,)])

    monkeypatch.setattr(dbBroker.oracledb, "connect", maybe)
    dbBroker.run_query_json("select 1 from dual")
    state["up"] = True
    logger.clear()
    dbBroker.run_query_json("select 1 from dual")
    messages = [e["message"] for e in logger.get_logs() if e["source"] == "DB"]
    assert any("restored" in m.lower() for m in messages)


def test_run_query_on_reuses_an_open_session(active_conn):
    """The collector runs every tracked query on one session."""
    conn = FakeConnection(description=[("A",)], rows=[(1,)])
    for _ in range(5):
        assert dbBroker.run_query_on(conn, "select 1 from dual", "1") == [{"A": 1}]
    assert conn.closed is False          # caller owns the lifetime
    assert len(conn.executed) == 5


def test_run_query_on_quiet_suppresses_logging(active_conn):
    from logBroker import logger
    conn = FakeConnection(raise_on_execute=oracle_error(942, "table or view does not exist"))
    logger.clear()
    result = dbBroker.run_query_on(conn, "select * from nope", "1", quiet=True)
    assert result[0]["error"].startswith("ORA-942")
    assert _levels() == []


def test_volatile_connection_ids_do_not_defeat_throttling():
    """The driver stamps a fresh CONNECTION_ID into every failure message."""
    a = dbBroker._failure_signature("ORA-0: DPY-6005: cannot connect (CONNECTION_ID=aaa==)")
    b = dbBroker._failure_signature("ORA-0: DPY-6005: cannot connect (CONNECTION_ID=bbb==)")
    assert a == b


def test_a_different_fault_still_logs(active_conn, monkeypatch):
    from logBroker import logger
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
    monkeypatch.setattr(dbBroker, "_connect_failures", {})
    errors = [oracle_error(12541, "TNS:no listener"),
              oracle_error(1017, "invalid username/password")]

    def boom(**kwargs):
        raise errors[min(len(logger.get_logs()), 1)] if False else errors.pop(0)

    monkeypatch.setattr(dbBroker.oracledb, "connect", boom)
    logger.clear()
    dbBroker.run_query_json("select 1 from dual")
    dbBroker.run_query_json("select 1 from dual")
    assert _levels().count("ERROR") == 2
