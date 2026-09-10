# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shared test fixtures.

Every module resolves data files through storage.data_file() at call time, so
pointing the data dir at a tmpdir fully isolates a test from the developer's
real connections.json, query library and history databases.
"""
import os
import tempfile

import pytest

import storage

# main.py builds its app at import time. Redirect writes into a throwaway
# directory *before* that happens so importing the suite never touches the
# working copy.
_IMPORT_SANDBOX = tempfile.mkdtemp(prefix="odbm-import-")
storage.set_data_dir(_IMPORT_SANDBOX)

import configBroker  # noqa: E402
import cryptoBroker  # noqa: E402
import dbBroker  # noqa: E402
import historyBroker  # noqa: E402
from logBroker import logger  # noqa: E402

SEED_QUERIES = {
    "1": {
        "id": "1", "desc": "Wait Classes", "sql": "select 1 from dual",
        "mode": "QUERY", "refreshOptimum": 60, "historyName": "Wait Classes",
    },
    "2": {
        "id": "2", "desc": "Plain query", "sql": "select 2 from dual",
        "mode": "QUERY", "refreshOptimum": 5,
    },
    "14": {
        "id": "14", "desc": "Show Plan Details", "mode": "QUERY",
        "sql": "SELECT * FROM table (DBMS_XPLAN.DISPLAY_CURSOR ( sql_id=>:sql_id, format=> 'typical'))",
    },
    "CMD_1": {
        "id": "CMD_1", "desc": "A command", "sql": "alter system flush shared_pool",
        "mode": "COMMAND",
    },
}


@pytest.fixture
def no_bundled_library(tmp_path):
    """
    Point resource lookups at an empty dir so the shipped sqlLib.xml fallback
    stays out of tests that are checking the no-library path.
    """
    empty = tmp_path / "empty-bundle"
    empty.mkdir(exist_ok=True)
    storage.set_resource_dir(str(empty))
    yield empty
    storage.set_resource_dir(None)


@pytest.fixture
def data_dir(tmp_path):
    """Fresh, isolated data directory with singleton state reset."""
    storage.set_data_dir(str(tmp_path))
    cryptoBroker.reset_cache()
    dbBroker.close_pools()
    historyBroker.history._writes_since_prune = 0
    logger.clear()
    yield tmp_path
    dbBroker.close_pools()
    storage.set_data_dir(_IMPORT_SANDBOX)
    storage.set_resource_dir(None)


@pytest.fixture
def seeded(data_dir):
    """Data dir pre-populated with a small, predictable query library."""
    storage.write_json("queries.json", SEED_QUERIES)
    storage.write_json("settings.json", {"wait_classes": "1"})
    return data_dir


@pytest.fixture
def app(seeded):
    import main
    return main.create_app()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture
def active_conn(data_dir):
    """A saved, selected connection so DB-facing code has credentials."""
    configBroker.save_config({
        "connections": [{
            "id": "conn-a", "name": "Test", "user": "scott",
            "password": "tiger", "dsn": "host:1521/FREE", "sysdba": False,
        }],
        "active_index": 0,
    })
    return configBroker.get_active_connection()


# ---------------------------------------------------------------- DB doubles

class FakeCursor:
    def __init__(self, conn, description, rows, raise_on_execute=None):
        self._conn = conn
        self.description = description
        self._rows = rows
        self._raise = raise_on_execute
        self.rowfactory = None
        self.rowcount = len(rows) if rows else 0
        self.executed = []

    def execute(self, sql, binds=None):
        self.executed.append((sql, binds))
        self._conn.executed.append((sql, binds))
        if self._raise:
            raise self._raise

    def fetchall(self):
        if self.rowfactory:
            return [self.rowfactory(*r) for r in self._rows]
        return list(self._rows)


class FakeConnection:
    def __init__(self, description=None, rows=(), raise_on_execute=None, pool=None):
        self.description = description
        self.rows = rows
        self.raise_on_execute = raise_on_execute
        self.closed = False
        self.committed = False
        self.executed = []
        self._pool = pool

    def cursor(self):
        return FakeCursor(self, self.description, self.rows, self.raise_on_execute)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True
        if self._pool is not None:
            self._pool.released += 1


class FakePool:
    def __init__(self, factory):
        self._factory = factory
        self.acquired = 0
        self.released = 0
        self.closed = False

    def acquire(self):
        self.acquired += 1
        conn = self._factory()
        conn._pool = self
        return conn

    def close(self, force=False):
        self.closed = True


def oracle_error(code=942, message="table or view does not exist"):
    """A realistic oracledb.Error with the .args[0].code/.message shape."""
    import oracledb

    class _ErrorObject:
        pass

    obj = _ErrorObject()
    obj.code = code
    obj.message = message
    obj.full_code = f"ORA-{code}"
    return oracledb.DatabaseError(obj)
