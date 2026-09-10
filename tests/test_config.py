# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import os

import configBroker
import cryptoBroker
import storage


def _save(connections, active_index=0):
    return configBroker.save_config({"connections": connections, "active_index": active_index})


def test_empty_when_no_file(data_dir):
    assert configBroker.load_config() == {"connections": [], "active_index": -1}
    assert configBroker.get_active_connection() is None
    assert configBroker.get_active_connection_id() == "default"


def test_password_is_encrypted_on_disk(data_dir):
    _save([{"id": "a", "name": "A", "user": "u", "password": "s3cret", "dsn": "d"}])
    on_disk = storage.read_json("connections.json")
    stored = on_disk["connections"][0]["password"]
    assert "s3cret" not in stored
    assert cryptoBroker.is_encrypted(stored)


def test_config_file_is_owner_only(data_dir):
    _save([{"id": "a", "user": "u", "password": "p", "dsn": "d"}])
    mode = os.stat(data_dir / "connections.json").st_mode & 0o777
    assert mode == 0o600


def test_public_config_never_exposes_passwords(data_dir):
    _save([{"id": "a", "name": "A", "user": "u", "password": "s3cret", "dsn": "d"}])
    public = configBroker.get_public_config()
    entry = public["connections"][0]
    assert entry["password"] == ""
    assert entry["has_password"] is True
    assert "s3cret" not in str(public)


def test_has_password_false_when_none_stored(data_dir):
    _save([{"id": "a", "user": "u", "password": "", "dsn": "d"}])
    assert configBroker.get_public_config()["connections"][0]["has_password"] is False


def test_active_connection_decrypts(data_dir):
    _save([{"id": "a", "user": "u", "password": "s3cret", "dsn": "host/db"}])
    conn = configBroker.get_active_connection()
    assert conn["password"] == "s3cret"
    assert configBroker.get_active_connection_id() == "u@host/db"


def test_blank_password_on_resave_keeps_the_secret(data_dir):
    _save([{"id": "a", "user": "u", "password": "s3cret", "dsn": "d"}])
    # The UI round-trips a redacted config; blank must mean "unchanged".
    _save([{"id": "a", "name": "renamed", "user": "u", "password": "", "dsn": "d"}])
    assert configBroker.get_active_connection()["password"] == "s3cret"
    assert configBroker.load_config()["connections"][0]["name"] == "renamed"


def test_supplied_password_overwrites(data_dir):
    _save([{"id": "a", "user": "u", "password": "old", "dsn": "d"}])
    _save([{"id": "a", "user": "u", "password": "new", "dsn": "d"}])
    assert configBroker.get_active_connection()["password"] == "new"


def test_deleting_first_connection_keeps_the_others_password(data_dir):
    # Regression: index-based merging would move A's password onto B.
    _save([
        {"id": "a", "user": "ua", "password": "pw-a", "dsn": "da"},
        {"id": "b", "user": "ub", "password": "pw-b", "dsn": "db"},
    ], active_index=1)

    public = configBroker.get_public_config()
    public["connections"].pop(0)
    public["active_index"] = 0
    configBroker.save_config(public)

    conn = configBroker.get_active_connection()
    assert conn["user"] == "ub"
    assert conn["password"] == "pw-b"


def test_reordering_connections_keeps_passwords_matched(data_dir):
    _save([
        {"id": "a", "user": "ua", "password": "pw-a", "dsn": "da"},
        {"id": "b", "user": "ub", "password": "pw-b", "dsn": "db"},
    ])
    public = configBroker.get_public_config()
    public["connections"].reverse()
    configBroker.save_config(public)

    stored = configBroker.load_config()["connections"]
    assert cryptoBroker.decrypt(stored[0]["password"]) == "pw-b"
    assert cryptoBroker.decrypt(stored[1]["password"]) == "pw-a"


def test_missing_ids_are_backfilled(data_dir):
    storage.write_json("connections.json", {
        "connections": [{"name": "legacy", "user": "u", "password": "p", "dsn": "d"}],
        "active_index": 0,
    })
    cfg = configBroker.load_config()
    assert cfg["connections"][0]["id"]
    # Persisted, so the next save can merge against it.
    assert storage.read_json("connections.json")["connections"][0]["id"]


def test_plaintext_migration_encrypts_in_place(data_dir):
    storage.write_json("connections.json", {
        "connections": [{"id": "a", "user": "u", "password": "legacyPlain", "dsn": "d"}],
        "active_index": 0,
    })
    assert configBroker.migrate_plaintext_passwords() == 1
    assert cryptoBroker.is_encrypted(storage.read_json("connections.json")["connections"][0]["password"])
    assert configBroker.get_active_connection()["password"] == "legacyPlain"
    # Idempotent: a second pass has nothing to do.
    assert configBroker.migrate_plaintext_passwords() == 0


def test_out_of_range_active_index_is_reset(data_dir):
    storage.write_json("connections.json", {"connections": [], "active_index": 5})
    assert configBroker.load_config()["active_index"] == -1
    assert configBroker.get_active_connection() is None


def test_garbage_config_does_not_crash(data_dir):
    storage.write_json("connections.json", ["not", "a", "dict"])
    assert configBroker.load_config() == {"connections": [], "active_index": -1}


def test_non_dict_connection_entries_are_dropped(data_dir):
    storage.write_json("connections.json", {
        "connections": ["junk", {"id": "a", "user": "u", "password": "p", "dsn": "d"}],
        "active_index": 0,
    })
    cfg = configBroker.load_config()
    assert len(cfg["connections"]) == 1
    assert cfg["connections"][0]["user"] == "u"
