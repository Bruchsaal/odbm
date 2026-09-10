# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import uuid

import cryptoBroker
import storage
from logBroker import logger

CONFIG_FILE = "connections.json"

EMPTY = {"connections": [], "active_index": -1}


def _blank():
    return {"connections": [], "active_index": -1}


def _normalize(cfg):
    """
    Coerce whatever is on disk into the expected shape and guarantee every
    connection carries a stable id. Returns (config, changed).
    """
    changed = False
    if not isinstance(cfg, dict):
        return _blank(), False

    conns = cfg.get("connections")
    if not isinstance(conns, list):
        conns = []
        changed = True

    clean = []
    for entry in conns:
        if not isinstance(entry, dict):
            changed = True
            continue
        if not entry.get("id"):
            entry["id"] = uuid.uuid4().hex
            changed = True
        entry.setdefault("name", "")
        entry.setdefault("user", "")
        entry.setdefault("dsn", "")
        entry.setdefault("password", "")
        entry["sysdba"] = bool(entry.get("sysdba"))
        # Whether the background collector polls this connection.
        entry["collect"] = bool(entry.get("collect", False))
        clean.append(entry)

    try:
        idx = int(cfg.get("active_index", -1))
    except (TypeError, ValueError):
        idx = -1
        changed = True
    if idx < -1 or idx >= len(clean):
        idx = -1
        changed = True

    return {"connections": clean, "active_index": idx}, changed


def load_config():
    """Stored configuration. Passwords stay encrypted at this layer."""
    raw = storage.read_json(CONFIG_FILE, default=None)
    if raw is None:
        return _blank()

    cfg, changed = _normalize(raw)
    if changed:
        # Backfill ids / repair shape once, so later saves merge correctly.
        _write(cfg)
    return cfg


def get_public_config():
    """
    Configuration safe to hand to the browser: no password material leaves
    the process, only a flag saying whether one is stored.
    """
    cfg = load_config()
    public = []
    for c in cfg["connections"]:
        public.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "user": c.get("user", ""),
            "dsn": c.get("dsn", ""),
            "sysdba": bool(c.get("sysdba")),
            "collect": bool(c.get("collect")),
            "password": "",
            "has_password": bool(c.get("password")),
        })
    return {"connections": public, "active_index": cfg["active_index"]}


def _write(cfg):
    storage.write_json(CONFIG_FILE, cfg, private=True)


def save_config(incoming):
    """
    Persist configuration from the UI. A blank password means "unchanged":
    the existing secret is carried over, matched by stable connection id, so
    reordering or deleting entries never reassigns the wrong password.
    """
    cfg, _ = _normalize(incoming)
    stored = {c["id"]: c for c in load_config()["connections"]}

    for conn in cfg["connections"]:
        supplied = conn.get("password") or ""
        if supplied:
            conn["password"] = cryptoBroker.encrypt(supplied)
        else:
            previous = stored.get(conn["id"], {})
            conn["password"] = previous.get("password", "")

    _write(cfg)
    return cfg


def get_active_connection():
    """The selected connection with its password decrypted, or None."""
    cfg = load_config()
    idx = cfg["active_index"]
    if idx < 0 or idx >= len(cfg["connections"]):
        return None

    conn = dict(cfg["connections"][idx])
    conn["password"] = cryptoBroker.decrypt(conn.get("password", ""))
    return conn


def connection_id(conn):
    """Stable label for a connection, used to name its history store."""
    if not conn:
        return "default"
    return f"{conn.get('user')}@{conn.get('dsn')}"


def get_active_connection_id():
    """Stable label for the active connection, used to name history stores."""
    return connection_id(get_active_connection())


def get_collecting_connections():
    """
    Every connection the background collector should poll, with decrypted
    passwords. The active connection is always included so the dashboard's
    own database is trended without extra configuration.
    """
    cfg = load_config()
    active = cfg["active_index"]
    out = []
    for i, conn in enumerate(cfg["connections"]):
        if not (conn.get("collect") or i == active):
            continue
        if not conn.get("user") or not conn.get("dsn"):
            continue
        c = dict(conn)
        c["password"] = cryptoBroker.decrypt(c.get("password", ""))
        out.append(c)
    return out


def migrate_plaintext_passwords():
    """Encrypt any password still stored in the clear from an older version."""
    cfg = load_config()
    touched = 0
    for conn in cfg["connections"]:
        pw = conn.get("password") or ""
        if pw and not cryptoBroker.is_encrypted(pw):
            conn["password"] = cryptoBroker.encrypt(pw)
            touched += 1
    if touched:
        _write(cfg)
        logger.info("Config", f"Encrypted {touched} stored password(s)")
    return touched
