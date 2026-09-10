# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import re
import threading
import time
from contextlib import contextmanager

import oracledb

from configBroker import get_active_connection
from logBroker import logger

POOL_MIN = 1
POOL_MAX = 8
POOL_TIMEOUT = 10          # seconds to wait for a free pooled session
POOL_RETRY_AFTER = 60      # back off this long after a failed pool creation

# try init thick client. ignore if unavailable - thin mode is the default.
try:
    oracledb.init_oracle_client(lib_dir=None)
except Exception:
    pass

_pools = {}
_pool_failures = {}
_pool_lock = threading.Lock()

_container_cache = {}
_container_lock = threading.Lock()

# "Nothing configured yet" is a setup state, not a failure, and it repeats on
# every polled widget - so it is logged as a warning and only once per change.
_warned_unconfigured = False

# Connection failures repeat for every query in every collector cycle, so
# they are logged on transition only, keyed by connection.
_connect_failures = {}

# CON_ID is 0 on a non-CDB, 1 in CDB$ROOT, 2 for PDB$SEED and >2 for a real PDB.
# v$database.CDB and v$pdbs do not exist before 12c, so the whole probe is
# treated as "not a CDB" if it raises.
CONTAINER_PROBE = """
SELECT SYS_CONTEXT('USERENV', 'CON_NAME')             AS con_name,
       TO_NUMBER(SYS_CONTEXT('USERENV', 'CON_ID'))    AS con_id,
       (SELECT cdb FROM v$database)                   AS is_cdb,
       (SELECT COUNT(*) FROM v$pdbs
         WHERE con_id > 2 AND open_mode LIKE 'READ%') AS open_pdbs
FROM dual
"""

# How many containers this account can actually see through a cdb_* view.
# Without CONTAINER_DATA a common user sees only the root, which would make a
# switch to cdb_* look successful while changing nothing.
VISIBILITY_PROBE = "SELECT COUNT(DISTINCT con_id) FROM cdb_tablespaces"

NON_CDB = {
    "is_cdb": False,
    "con_name": None,
    "con_id": 0,
    "in_root": False,
    "open_pdbs": 0,
    "visible_containers": None,
    "container_data_ok": True,
    "scope": "Whole database (not multitenant)",
    "warning": None,
    "grant_hint": None,
}


class ConnectionUnavailable(Exception):
    """Raised when no usable database session could be obtained."""


def _pool_key(creds):
    # The password is part of the key so that editing credentials retires the
    # old pool instead of reusing sessions opened with a stale password.
    fingerprint = hashlib.sha256((creds.get("password") or "").encode()).hexdigest()[:16]
    return (creds.get("user"), creds.get("dsn"), bool(creds.get("sysdba")), fingerprint)


def _get_pool(creds):
    """
    Return a session pool for these credentials, or None to connect directly.
    SYSDBA sessions are deliberately not pooled.
    """
    if creds.get("sysdba"):
        return None

    key = _pool_key(creds)
    with _pool_lock:
        if key in _pools:
            return _pools[key]

        failed_at = _pool_failures.get(key)
        if failed_at and (time.monotonic() - failed_at) < POOL_RETRY_AFTER:
            return None

        try:
            pool = oracledb.create_pool(
                user=creds["user"],
                password=creds["password"],
                dsn=creds["dsn"],
                min=POOL_MIN,
                max=POOL_MAX,
                increment=1,
                getmode=oracledb.POOL_GETMODE_WAIT,
                wait_timeout=POOL_TIMEOUT * 1000,
            )
        except Exception as e:
            _pool_failures[key] = time.monotonic()
            logger.warn("DB", "Session pool unavailable, using direct connections", str(e))
            return None

        _pool_failures.pop(key, None)
        _pools[key] = pool
        logger.info("DB", f"Opened session pool for {creds.get('dsn')}")
        return pool


def _classify_container(row, visible):
    """Turn the probe rows into the container context the UI renders."""
    con_name = row.get("CON_NAME")
    con_id = int(row.get("CON_ID") or 0)
    open_pdbs = int(row.get("OPEN_PDBS") or 0)
    in_root = con_id == 1

    ctx = {
        "is_cdb": True,
        "con_name": con_name,
        "con_id": con_id,
        "in_root": in_root,
        "open_pdbs": open_pdbs,
        "visible_containers": visible,
        "container_data_ok": True,
        "warning": None,
        "grant_hint": None,
    }

    if not in_root:
        # Connected straight to a PDB: dba_* is correctly scoped to it.
        ctx["scope"] = f"PDB {con_name}"
        return ctx

    ctx["scope"] = f"{con_name} (container root)"
    if open_pdbs:
        # The shipped queries use dba_*, which is root-scoped even for a user
        # that holds CONTAINER_DATA.
        ctx["warning"] = (
            f"Space and object figures cover {con_name} only - "
            f"{open_pdbs} open PDB(s) are not included. "
            f"Connect to the PDB service directly for per-PDB numbers."
        )
        if visible is not None and visible <= 1:
            ctx["container_data_ok"] = False
            ctx["grant_hint"] = (
                "This account can only see one container through cdb_* views. "
                "Grant cross-container visibility with: "
                "ALTER USER <user> SET CONTAINER_DATA = ALL CONTAINER = CURRENT;"
            )
    return ctx


def get_container_context(force=False, creds=None, key=None):
    """
    Detect whether this connection lands in a CDB root, a PDB or a non-CDB.

    Probed once per connection and cached; any Oracle error means the server
    predates multitenant, which is reported as a plain non-CDB rather than
    failing the request.
    """
    from configBroker import get_active_connection_id, connection_id

    if key is None:
        key = connection_id(creds) if creds else get_active_connection_id()
    if not force:
        with _container_lock:
            if key in _container_cache:
                return _container_cache[key]

    ctx = dict(NON_CDB)
    try:
        with acquire(creds) as conn:
            cursor = conn.cursor()
            cursor.execute(CONTAINER_PROBE)
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
            row = dict(zip(columns, rows[0])) if rows else {}

            if str(row.get("IS_CDB") or "").upper() == "YES":
                visible = None
                try:
                    vcur = conn.cursor()
                    vcur.execute(VISIBILITY_PROBE)
                    vrow = vcur.fetchone()
                    visible = int(vrow[0]) if vrow else None
                except oracledb.Error:
                    visible = None  # no cdb_* access at all
                ctx = _classify_container(row, visible)
    except ConnectionUnavailable:
        return dict(NON_CDB)  # nothing to cache; retry on the next call
    except oracledb.Error as e:
        logger.debug("DB", "Container probe unavailable, assuming non-CDB", str(e))
    except Exception as e:
        logger.warn("DB", "Container probe failed, assuming non-CDB", str(e))

    if ctx.get("warning"):
        logger.warn("DB", ctx["warning"])
    if ctx.get("grant_hint"):
        logger.warn("DB", ctx["grant_hint"])

    with _container_lock:
        _container_cache[key] = ctx
    return ctx


def close_pools():
    """Tear down every pool. Called on shutdown and between tests."""
    with _container_lock:
        _container_cache.clear()
    with _pool_lock:
        for pool in _pools.values():
            try:
                pool.close(force=True)
            except Exception:
                pass
        _pools.clear()
        _pool_failures.clear()


@contextmanager
def acquire(creds=None):
    """
    Yield a database session and always hand it back. Pooled sessions return
    to the pool on close; direct sessions are closed outright. Every caller
    goes through here, so no code path can leak a session.

    `creds` targets a specific connection; omitted, it uses the active one.
    The background collector passes explicit credentials so it can poll
    several databases while the dashboard views just one.
    """
    global _warned_unconfigured
    if creds is None:
        creds = get_active_connection()
    if not creds:
        if not _warned_unconfigured:
            logger.warn("DB", "No database connection configured",
                        "Add a connection under Settings to start monitoring.")
            _warned_unconfigured = True
        raise ConnectionUnavailable("No database connection configured")
    _warned_unconfigured = False

    pool = _get_pool(creds)
    try:
        if pool is not None:
            conn = pool.acquire()
        else:
            mode = oracledb.SYSDBA if creds.get("sysdba") else oracledb.AUTH_MODE_DEFAULT
            conn = oracledb.connect(
                user=creds["user"],
                password=creds["password"],
                dsn=creds["dsn"],
                mode=mode,
            )
    except oracledb.Error as e:
        _log_connect_failure(creds, e)
        raise ConnectionUnavailable(_oracle_message(e))
    else:
        _clear_connect_failure(creds)

    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _failure_key(creds):
    return f"{creds.get('user')}@{creds.get('dsn')}"


def _failure_signature(message):
    """
    Stable identity for a connection error.

    The driver embeds a fresh CONNECTION_ID in every failure, so comparing raw
    messages would treat each retry as a new fault and defeat the throttling.
    """
    signature = re.sub(r"\([^)]*\)", "", message)
    return " ".join(signature.split())[:120]


def _log_connect_failure(creds, exc):
    key = _failure_key(creds)
    message = _oracle_message(exc)
    signature = _failure_signature(message)
    if _connect_failures.get(key) != signature:
        _connect_failures[key] = signature
        logger.error("DB", f"Connection failed to {creds.get('dsn')}", message)


def _clear_connect_failure(creds):
    key = _failure_key(creds)
    if _connect_failures.pop(key, None) is not None:
        logger.info("DB", f"Connection restored to {creds.get('dsn')}")


def run_query_on(conn, sql_text, query_id=None, binds=None, quiet=False):
    """
    Execute a query on an already-open session.

    Lets the collector open one session per database per cycle instead of one
    per query - which against an unreachable listener is the difference
    between a single failed connect and one timeout for every tracked query.
    """
    source_tag = f"Query {query_id}" if query_id else "Ad-Hoc"
    try:
        cursor = conn.cursor()
        cursor.execute(sql_text, binds or {})
        if cursor.description:
            columns = [col[0] for col in cursor.description]
            cursor.rowfactory = lambda *args: dict(zip(columns, args))
            data = cursor.fetchall()
            if quiet:
                return data
            if len(data) == 0:
                logger.info("DB", f"{source_tag} returned 0 rows", f"Executed SQL:\n{sql_text}")
            elif query_id:
                logger.info("DB", f"{source_tag} returned {len(data)} rows")
            return data
        return [{"status": "Success"}]
    except oracledb.Error as e:
        msg = _oracle_message(e)
        if not quiet:
            logger.error("DB", f"{source_tag} Failed", f"Error: {msg}\n\nSQL:\n{sql_text}")
        return [{"error": msg}]
    except Exception as e:
        if not quiet:
            logger.error("DB", f"{source_tag} Unexpected Error", str(e))
        return [{"error": str(e)}]


def _oracle_message(exc):
    try:
        error_obj = exc.args[0]
        return f"ORA-{error_obj.code}: {error_obj.message}"
    except (AttributeError, IndexError):
        return str(exc)


def run_query_json(sql_text, query_id=None, binds=None, creds=None):
    """Execute a read query on a fresh session and return rows as dicts."""
    try:
        with acquire(creds) as conn:
            return run_query_on(conn, sql_text, query_id, binds)
    except ConnectionUnavailable as e:
        return [{"error": str(e)}]


def run_command_json(sql_text, binds=None, creds=None):
    """Execute a DML/DDL statement and commit."""
    try:
        with acquire(creds) as conn:
            cursor = conn.cursor()
            cursor.execute(sql_text, binds or {})
            row_count = cursor.rowcount
            conn.commit()
            logger.info("DB", f"Command affected {row_count} row(s)")
            return [{
                "status": "Success",
                "message": "Command executed successfully.",
                "rows_affected": row_count,
            }]

    except ConnectionUnavailable as e:
        return [{"error": str(e)}]
    except oracledb.Error as e:
        msg = _oracle_message(e)
        logger.error("DB", "Command Failed", f"Error: {msg}\n\nSQL:\n{sql_text}")
        return [{"error": msg}]
    except Exception as e:
        logger.error("DB", "Command Unexpected Error", str(e))
        return [{"error": str(e)}]
