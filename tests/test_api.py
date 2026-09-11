# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from conftest import posix_only

import configBroker
import main
import storage


@pytest.fixture
def fake_db(monkeypatch):
    """Intercept SQL execution so route tests need no Oracle."""
    calls = []

    def fake_query(sql, query_id=None, binds=None):
        calls.append({"sql": sql, "query_id": query_id, "binds": binds, "kind": "query"})
        return fake_query.result

    def fake_command(sql, binds=None):
        calls.append({"sql": sql, "binds": binds, "kind": "command"})
        return [{"status": "Success", "rows_affected": 1}]

    fake_query.result = [{"AAS": 1.0}]
    monkeypatch.setattr(main, "run_query_json", fake_query)
    monkeypatch.setattr(main, "run_command_json", fake_command)
    return calls, fake_query


# ------------------------------------------------------------------ bootstrap

def test_boot_creates_data_files(data_dir, no_bundled_library):
    main.check_and_deploy_data()
    assert storage.read_json("queries.json") == {}
    assert storage.read_json("settings.json") == {}
    assert storage.read_json("connections.json") == {"connections": [], "active_index": -1}


def test_boot_creates_queries_json_as_an_object(data_dir, no_bundled_library):
    """Regression: it used to be seeded as [], which broke get_query()."""
    main.check_and_deploy_data()
    assert isinstance(storage.read_json("queries.json"), dict)


def test_boot_never_overwrites_user_data(seeded):
    main.check_and_deploy_data()
    assert storage.read_json("queries.json")["1"]["refreshOptimum"] == 60


@posix_only
def test_boot_writes_a_private_credentials_file(data_dir, no_bundled_library):
    import os
    main.check_and_deploy_data()
    mode = os.stat(data_dir / "connections.json").st_mode & 0o777
    assert mode == 0o600


def test_index_and_static_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


# --------------------------------------------------------------------- config

def test_get_config_never_returns_passwords(client):
    configBroker.save_config({"connections": [{
        "id": "a", "name": "A", "user": "u", "password": "s3cret", "dsn": "d"}],
        "active_index": 0})

    res = client.get("/api/config")
    assert res.status_code == 200
    assert "s3cret" not in res.text
    entry = res.json()["connections"][0]
    assert entry["password"] == ""
    assert entry["has_password"] is True


def test_post_config_round_trip_keeps_password(client):
    configBroker.save_config({"connections": [{
        "id": "a", "name": "A", "user": "u", "password": "s3cret", "dsn": "d"}],
        "active_index": 0})

    # Exactly what the browser sends back: the redacted payload.
    payload = client.get("/api/config").json()
    payload["connections"][0]["name"] = "Renamed"
    assert client.post("/api/config", json=payload).status_code == 200

    assert configBroker.get_active_connection()["password"] == "s3cret"
    assert configBroker.load_config()["connections"][0]["name"] == "Renamed"


def test_post_config_stores_a_new_password_encrypted(client):
    client.post("/api/config", json={"connections": [{
        "id": "a", "name": "A", "user": "u", "password": "brand-new", "dsn": "d"}],
        "active_index": 0})
    assert "brand-new" not in (storage.read_json("connections.json").__repr__())
    assert configBroker.get_active_connection()["password"] == "brand-new"


# -------------------------------------------------------------------- library

def test_get_library_is_sorted(client):
    ids = [q["id"] for q in client.get("/api/library").json()]
    assert ids == ["1", "2", "14", "CMD_1"]


def test_saving_a_query_preserves_refresh_optimum(client):
    """The headline regression, end to end through the API."""
    res = client.post("/api/library", json={
        "id": "1", "desc": "edited", "sql": "select 9 from dual", "mode": "QUERY"})
    assert res.status_code == 200
    assert res.json()["item"]["refreshOptimum"] == 60

    stored = {q["id"]: q for q in client.get("/api/library").json()}
    assert stored["1"]["refreshOptimum"] == 60
    assert stored["1"]["historyName"] == "Wait Classes"
    assert stored["1"]["desc"] == "edited"


def test_saving_a_query_can_update_refresh_optimum(client):
    client.post("/api/library", json={
        "id": "1", "desc": "d", "sql": "s", "mode": "QUERY", "refreshOptimum": 30})
    stored = {q["id"]: q for q in client.get("/api/library").json()}
    assert stored["1"]["refreshOptimum"] == 30


def test_saving_a_query_can_set_history_name(client):
    client.post("/api/library", json={
        "id": "2", "desc": "d", "sql": "s", "mode": "QUERY", "historyName": "New Metric"})
    stored = {q["id"]: q for q in client.get("/api/library").json()}
    assert stored["2"]["historyName"] == "New Metric"


def test_saving_a_new_query(client):
    client.post("/api/library", json={"id": "77", "desc": "n", "sql": "select 1", "mode": "QUERY"})
    assert any(q["id"] == "77" for q in client.get("/api/library").json())


def test_blank_id_is_rejected(client):
    assert client.post("/api/library", json={
        "id": "  ", "desc": "d", "sql": "s", "mode": "QUERY"}).status_code == 400


def test_delete_query(client):
    assert client.delete("/api/library/1").status_code == 200
    assert client.delete("/api/library/1").status_code == 404


# --------------------------------------------------------------------- metric

def test_metric_returns_data(client, fake_db):
    calls, cfg = fake_db
    body = client.get("/api/metric/1").json()
    assert body == {"data": [{"AAS": 1.0}]}
    assert calls[0]["sql"] == "select 1 from dual"


def test_unknown_metric_id_reports_an_error(client, fake_db):
    body = client.get("/api/metric/does-not-exist").json()
    assert "not found" in body["data"][0]["error"]


def test_metric_records_history_for_tagged_query(client, fake_db, monkeypatch):
    configBroker.save_config({"connections": [{
        "id": "a", "user": "scott", "password": "p", "dsn": "host/db"}], "active_index": 0})

    client.get("/api/metric/1?history=true")
    options = client.get("/api/history/options").json()
    assert options == {"Wait Classes": ["AAS"]}


def test_metric_skips_history_for_untagged_query(client, fake_db):
    configBroker.save_config({"connections": [{
        "id": "a", "user": "scott", "password": "p", "dsn": "host/db"}], "active_index": 0})

    client.get("/api/metric/2?history=true")   # query 2 has no historyName
    assert client.get("/api/history/options").json() == {}


def test_metric_respects_the_history_switch(client, fake_db):
    configBroker.save_config({"connections": [{
        "id": "a", "user": "scott", "password": "p", "dsn": "host/db"}], "active_index": 0})

    client.get("/api/metric/1?history=false")
    assert client.get("/api/history/options").json() == {}


def test_metric_does_not_record_errors(client, fake_db):
    calls, cfg = fake_db
    cfg.result = [{"error": "ORA-942"}]
    configBroker.save_config({"connections": [{
        "id": "a", "user": "scott", "password": "p", "dsn": "host/db"}], "active_index": 0})

    client.get("/api/metric/1?history=true")
    assert client.get("/api/history/options").json() == {}


# -------------------------------------------------------------------- execute

def test_execute_passes_bind_variables(client, fake_db):
    calls, cfg = fake_db
    client.post("/api/execute", json={
        "sql": "select sql_text from v$sqltext where sql_id=:sql_id",
        "binds": {"sql_id": "abc123"}})
    assert calls[0]["binds"] == {"sql_id": "abc123"}


def test_execute_without_binds_still_works(client, fake_db):
    calls, cfg = fake_db
    client.post("/api/execute", json={"sql": "select 1 from dual"})
    assert calls[0]["binds"] == {}


def test_execute_command(client, fake_db):
    calls, cfg = fake_db
    body = client.post("/api/execute/command", json={"sql": "alter system checkpoint"}).json()
    assert body["data"][0]["status"] == "Success"
    assert calls[0]["kind"] == "command"


# -------------------------------------------------------------- widgets, logs

def test_widget_defaults_are_served_and_persisted(client):
    body = client.get("/api/widgets").json()
    assert body["wait_classes"] == "1"

    client.post("/api/widgets", json={"settings": {"wait_classes": "42"}})
    assert client.get("/api/widgets").json()["wait_classes"] == "42"


def test_widget_settings_merge_in_new_defaults(client):
    client.post("/api/widgets", json={"settings": {"wait_classes": "42"}})
    body = client.get("/api/widgets").json()
    assert body["wait_classes"] == "42"
    assert "sessions_tab" in body     # key the saved file never had


def test_logs_can_be_read_and_cleared(client):
    assert isinstance(client.get("/api/logs").json(), list)
    assert client.post("/api/logs/clear").json() == {"status": "cleared"}
    assert client.get("/api/logs").json() == []


# -------------------------------------------------------------------- history

def test_history_clear(client, fake_db):
    configBroker.save_config({"connections": [{
        "id": "a", "user": "scott", "password": "p", "dsn": "host/db"}], "active_index": 0})
    client.get("/api/metric/1?history=true")
    assert client.get("/api/history/options").json() != {}

    client.post("/api/history/clear")
    assert client.get("/api/history/options").json() == {}


def test_history_endpoints_are_safe_with_no_connection(client):
    assert client.get("/api/history/options").json() == {}
    assert client.get("/api/history/data?source=S").json() == {}


def test_missing_static_dir_does_not_kill_the_api(data_dir, no_bundled_library):
    """A broken build should still surface a readable error, not a traceback."""
    from fastapi.testclient import TestClient
    app = main.create_app()
    with TestClient(app) as c:
        assert c.get("/api/logs").status_code == 200
        assert c.get("/").status_code == 500


def test_repeated_redacted_round_trips_never_lose_the_password(client):
    """The UI re-posts config on every connection switch; it must be lossless."""
    client.post("/api/config", json={"connections": [
        {"id": "", "name": "A", "user": "ua", "password": "pw-a", "dsn": "da"},
        {"id": "", "name": "B", "user": "ub", "password": "pw-b", "dsn": "db"},
    ], "active_index": 0})

    for cycle in range(5):
        payload = client.get("/api/config").json()
        assert all(c["has_password"] for c in payload["connections"]), f"lost at cycle {cycle}"
        payload["active_index"] = cycle % 2
        assert client.post("/api/config", json=payload).status_code == 200

    stored = configBroker.load_config()["connections"]
    import cryptoBroker
    assert cryptoBroker.decrypt(stored[0]["password"]) == "pw-a"
    assert cryptoBroker.decrypt(stored[1]["password"]) == "pw-b"


def test_new_connections_get_distinct_ids(client):
    client.post("/api/config", json={"connections": [
        {"id": "", "name": "A", "user": "ua", "password": "pw-a", "dsn": "da"},
        {"id": "", "name": "B", "user": "ub", "password": "pw-b", "dsn": "db"},
    ], "active_index": 0})
    ids = [c["id"] for c in client.get("/api/config").json()["connections"]]
    assert len(set(ids)) == 2 and all(ids)


def test_switching_active_connection_switches_history_store(client, fake_db):
    client.post("/api/config", json={"connections": [
        {"id": "", "name": "A", "user": "ua", "password": "p", "dsn": "da"},
        {"id": "", "name": "B", "user": "ub", "password": "p", "dsn": "db"},
    ], "active_index": 0})
    client.get("/api/metric/1?history=true")
    assert client.get("/api/history/options").json() == {"Wait Classes": ["AAS"]}

    payload = client.get("/api/config").json()
    payload["active_index"] = 1
    client.post("/api/config", json=payload)
    # Second connection starts with its own, empty history.
    assert client.get("/api/history/options").json() == {}


@posix_only
def test_deployed_data_files_are_not_owner_only(data_dir):
    """
    shutil.copy2 preserved PyInstaller's restrictive _MEIPASS mode, so a first
    run produced 0600 queries.json/settings.json regardless of umask.
    """
    import os
    import shutil
    bundle = data_dir / "bundle"
    bundle.mkdir()
    for name in ("queries.json", "settings.json"):
        src = bundle / name
        src.write_text("{}")
        os.chmod(src, 0o600)          # what PyInstaller extracts
    storage.set_resource_dir(str(bundle))
    try:
        main.check_and_deploy_data()
        umask = os.umask(0)
        os.umask(umask)
        for name in ("queries.json", "settings.json"):
            mode = os.stat(data_dir / name).st_mode & 0o777
            assert mode == (0o666 & ~umask), f"{name} deployed as {oct(mode)}"
    finally:
        storage.set_resource_dir(None)


@posix_only
def test_credentials_file_stays_owner_only(data_dir, no_bundled_library):
    """connections.json must remain 0600 even as the others widen."""
    import os
    main.check_and_deploy_data()
    assert (os.stat(data_dir / "connections.json").st_mode & 0o777) == 0o600


# --------------------------------------------------- collector and alerts

def test_collector_status_endpoint(client):
    body = client.get("/api/collector").json()
    assert body["enabled"] is False
    assert "interval" in body and "connections" in body


def test_collector_can_be_enabled_and_persists(client):
    import collectorBroker
    body = client.post("/api/collector", json={"enabled": True, "interval": 30}).json()
    try:
        assert body["enabled"] is True
        assert body["interval"] == 30
        assert collectorBroker.load_state() == {"enabled": True, "interval": 30}
    finally:
        client.post("/api/collector", json={"enabled": False, "interval": 30})


def test_collector_interval_is_floored_by_the_api(client):
    import collectorBroker
    body = client.post("/api/collector", json={"enabled": False, "interval": 1}).json()
    assert body["interval"] == collectorBroker.MIN_INTERVAL


def test_disabling_the_collector_stops_the_thread(client):
    from collectorBroker import collector
    client.post("/api/collector", json={"enabled": True, "interval": 30})
    assert collector.running is True
    client.post("/api/collector", json={"enabled": False, "interval": 30})
    assert collector.running is False


def test_alerts_endpoint_is_empty_by_default(client):
    body = client.get("/api/alerts").json()
    assert body == {"worst": "OK", "count": 0, "alerts": []}


def test_metric_endpoint_raises_alerts(client, fake_db):
    """Viewing a widget reacts immediately instead of awaiting the next cycle."""
    from alertBroker import alerts
    calls, cfg = fake_db
    alerts.clear()
    configBroker.save_config({"connections": [{
        "id": "a", "user": "scott", "password": "p", "dsn": "host/db"}], "active_index": 0})

    client.post("/api/library", json={
        "id": "1", "desc": "Wait Classes", "sql": "select 1", "mode": "QUERY",
        "alert": {"metric": "AAS", "warn": 1, "crit": 2}})
    cfg.result = [{"AAS": 5.0}]

    client.get("/api/metric/1")
    body = client.get("/api/alerts").json()
    assert body["worst"] == "CRIT"
    assert body["count"] == 1
    alerts.clear()


def test_alert_rule_survives_an_editor_save(client):
    """Like refreshOptimum, the rule must not be stripped by a UI save."""
    client.post("/api/library", json={
        "id": "2", "desc": "d", "sql": "s", "mode": "QUERY",
        "alert": {"metric": "X", "warn": 1, "crit": 2}})
    client.post("/api/library", json={"id": "2", "desc": "edited", "sql": "s2", "mode": "QUERY"})
    stored = {q["id"]: q for q in client.get("/api/library").json()}
    assert stored["2"]["alert"] == {"metric": "X", "warn": 1, "crit": 2}
    assert stored["2"]["desc"] == "edited"


def test_saving_config_clears_stale_alerts(client):
    from alertBroker import alerts
    alerts.evaluate({"id": "1", "desc": "x",
                     "alert": {"metric": "V", "warn": 1, "crit": 2}},
                    [{"V": 9}], "scott@host/db")
    assert alerts.active()
    client.post("/api/config", json={"connections": [], "active_index": -1})
    assert alerts.active() == []


def test_connections_carry_a_collect_flag(client):
    client.post("/api/config", json={"connections": [
        {"id": "", "name": "A", "user": "ua", "password": "p", "dsn": "da", "collect": True},
    ], "active_index": -1})
    assert client.get("/api/config").json()["connections"][0]["collect"] is True


# ------------------------------------------------------------ DNS rebinding

def test_rebinding_host_is_rejected(app):
    """
    Localhost binding and the absence of CORS do not stop DNS rebinding: a
    hostile page re-resolves its own domain to 127.0.0.1, and the browser then
    treats the response as same-origin. With no authentication that is
    arbitrary SQL, so unexpected Host headers must be refused.
    """
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        res = c.get("/api/config", headers={"Host": "evil.example.com"})
        assert res.status_code == 400


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "localhost:9000", "127.0.0.1:9000"])
def test_legitimate_hosts_are_accepted(app, host):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        assert c.get("/api/config", headers={"Host": host}).status_code == 200


def test_allowed_hosts_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("ODBM_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(main, "HOST", "127.0.0.1")
    assert "evil.example.com" not in main.allowed_hosts()
    assert "127.0.0.1" in main.allowed_hosts()


def test_allowed_hosts_can_be_overridden(monkeypatch):
    monkeypatch.setenv("ODBM_ALLOWED_HOSTS", "odbm.internal, 10.0.0.5")
    assert main.allowed_hosts() == ["odbm.internal", "10.0.0.5"]


def test_deliberate_exposure_does_not_break_access(monkeypatch):
    """Binding 0.0.0.0 is already a loud opt-out; do not also break the UI."""
    monkeypatch.delenv("ODBM_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(main, "HOST", "0.0.0.0")
    assert main.allowed_hosts() == ["*"]
