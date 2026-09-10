# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Container (CDB/PDB) detection.

dba_* views are container scoped: in a CDB root they silently return only the
root's data. These tests pin the detection that turns that silence into a
visible warning, and the fallbacks that keep it from breaking older servers.
"""
import pytest

import dbBroker
from conftest import oracle_error

PROBE_COLS = [("CON_NAME",), ("CON_ID",), ("IS_CDB",), ("OPEN_PDBS",)]


class ScriptedCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql, binds=None):
        self.conn.executed.append(sql)
        for needle, outcome in self.conn.script:
            if needle in sql:
                if isinstance(outcome, Exception):
                    raise outcome
                self.description, self._rows = outcome
                return
        raise AssertionError(f"unscripted SQL: {sql[:60]}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class ScriptedConnection:
    def __init__(self, script):
        self.script = script
        self.executed = []
        self.closed = False

    def cursor(self):
        return ScriptedCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def scripted(active_conn, monkeypatch):
    """Drive the probe with canned responses and count connections opened."""
    made = []

    def install(script):
        def connect(**kwargs):
            conn = ScriptedConnection(script)
            made.append(conn)
            return conn
        monkeypatch.setattr(dbBroker.oracledb, "connect", connect)
        monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
        return made

    return install


def probe(con_name, con_id, is_cdb, open_pdbs):
    return ("SYS_CONTEXT", (PROBE_COLS, [(con_name, con_id, is_cdb, open_pdbs)]))


def visible(n):
    return ("cdb_tablespaces", ([("COUNT",)], [(n,)]))


# ------------------------------------------------------------------ non-CDB

def test_non_cdb_reports_whole_database(scripted):
    scripted([probe("", 0, "NO", 0)])
    ctx = dbBroker.get_container_context()
    assert ctx["is_cdb"] is False
    assert ctx["warning"] is None
    assert ctx["scope"] == "Whole database (not multitenant)"


def test_pre_12c_probe_failure_falls_back_to_non_cdb(scripted):
    """v$database.CDB and v$pdbs do not exist on 11g — ORA-00904 / ORA-00942."""
    scripted([("SYS_CONTEXT", oracle_error(904, "invalid identifier"))])
    ctx = dbBroker.get_container_context()
    assert ctx["is_cdb"] is False
    assert ctx["warning"] is None


def test_probe_failure_does_not_raise(scripted):
    scripted([("SYS_CONTEXT", oracle_error(942, "table or view does not exist"))])
    assert dbBroker.get_container_context()["is_cdb"] is False


# ---------------------------------------------------------------------- PDB

def test_connected_to_a_pdb_is_correctly_scoped(scripted):
    """dba_* is right when you connect straight to the PDB — no warning."""
    scripted([probe("FREEPDB1", 3, "YES", 1), visible(1)])
    ctx = dbBroker.get_container_context()
    assert ctx["is_cdb"] is True
    assert ctx["in_root"] is False
    assert ctx["scope"] == "PDB FREEPDB1"
    assert ctx["warning"] is None
    assert ctx["grant_hint"] is None


# --------------------------------------------------------------------- root

def test_root_with_open_pdbs_warns(scripted):
    scripted([probe("CDB$ROOT", 1, "YES", 2), visible(3)])
    ctx = dbBroker.get_container_context()
    assert ctx["in_root"] is True
    assert "2 open PDB(s) are not included" in ctx["warning"]
    assert ctx["container_data_ok"] is True
    assert ctx["grant_hint"] is None


def test_root_without_container_data_gets_the_grant_hint(scripted):
    """cdb_* showing one container means CONTAINER_DATA is missing."""
    scripted([probe("CDB$ROOT", 1, "YES", 2), visible(1)])
    ctx = dbBroker.get_container_context()
    assert ctx["container_data_ok"] is False
    assert "CONTAINER_DATA = ALL" in ctx["grant_hint"]


def test_root_with_no_cdb_access_still_warns(scripted):
    """No cdb_* privilege at all must not suppress the scope warning."""
    scripted([probe("CDB$ROOT", 1, "YES", 1),
              ("cdb_tablespaces", oracle_error(942, "table or view does not exist"))])
    ctx = dbBroker.get_container_context()
    assert ctx["warning"] is not None
    assert ctx["visible_containers"] is None
    assert ctx["container_data_ok"] is True  # unknown, so not asserted as broken


def test_root_with_no_open_pdbs_does_not_warn(scripted):
    scripted([probe("CDB$ROOT", 1, "YES", 0), visible(1)])
    ctx = dbBroker.get_container_context()
    assert ctx["in_root"] is True
    assert ctx["warning"] is None


# -------------------------------------------------------------------- cache

def test_probe_runs_once_per_connection(scripted):
    made = scripted([probe("CDB$ROOT", 1, "YES", 1), visible(2)])
    for _ in range(5):
        dbBroker.get_container_context()
    assert len(made) == 1, "probe must be cached, not run per request"


def test_force_reprobes(scripted):
    made = scripted([probe("CDB$ROOT", 1, "YES", 1), visible(2)])
    dbBroker.get_container_context()
    dbBroker.get_container_context(force=True)
    assert len(made) == 2


def test_closing_pools_clears_the_cache(scripted):
    made = scripted([probe("CDB$ROOT", 1, "YES", 1), visible(2)])
    dbBroker.get_container_context()
    dbBroker.close_pools()          # called whenever the config changes
    dbBroker.get_container_context()
    assert len(made) == 2


def test_no_connection_is_not_cached(data_dir, monkeypatch):
    """A failed probe must not pin 'non-CDB' for the rest of the session."""
    monkeypatch.setattr(dbBroker, "_get_pool", lambda creds: None)
    ctx = dbBroker.get_container_context()
    assert ctx["is_cdb"] is False
    from configBroker import get_active_connection_id
    assert get_active_connection_id() not in dbBroker._container_cache


def test_probe_connection_is_returned(scripted):
    made = scripted([probe("CDB$ROOT", 1, "YES", 1), visible(2)])
    dbBroker.get_container_context()
    assert made[0].closed is True


# ---------------------------------------------------------------------- api

def test_dbinfo_endpoint(client, monkeypatch):
    monkeypatch.setattr(dbBroker, "get_container_context",
                        lambda *a, **k: {"is_cdb": True, "scope": "CDB$ROOT (container root)",
                                         "warning": "…", "grant_hint": None})
    import main
    monkeypatch.setattr(main, "get_container_context",
                        lambda *a, **k: dbBroker.get_container_context())
    body = client.get("/api/dbinfo").json()
    assert body["scope"] == "CDB$ROOT (container root)"
    assert body["warning"] == "…"
