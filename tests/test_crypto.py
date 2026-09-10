# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import os

import cryptoBroker
import storage


def test_round_trip(data_dir):
    token = cryptoBroker.encrypt("hunter2")
    assert token != "hunter2"
    assert cryptoBroker.is_encrypted(token)
    assert cryptoBroker.decrypt(token) == "hunter2"


def test_empty_password_stays_empty(data_dir):
    assert cryptoBroker.encrypt("") == ""
    assert cryptoBroker.decrypt("") == ""


def test_encrypt_is_idempotent(data_dir):
    token = cryptoBroker.encrypt("pw")
    assert cryptoBroker.encrypt(token) == token


def test_legacy_plaintext_passes_through(data_dir):
    # Values written before encryption existed carry no prefix.
    assert cryptoBroker.decrypt("oldPlaintext") == "oldPlaintext"


def test_key_file_is_owner_only(data_dir):
    cryptoBroker.encrypt("pw")
    key_path = data_dir / cryptoBroker.KEY_FILE
    assert key_path.exists()
    assert (os.stat(key_path).st_mode & 0o777) == 0o600


def test_key_is_stable_across_cache_resets(data_dir):
    token = cryptoBroker.encrypt("pw")
    cryptoBroker.reset_cache()
    assert cryptoBroker.decrypt(token) == "pw"


def test_corrupt_token_decrypts_to_empty_not_crash(data_dir):
    cryptoBroker.encrypt("pw")  # ensure a key exists
    assert cryptoBroker.decrypt(cryptoBroker.PREFIX + "garbage") == ""


def test_token_from_another_key_is_rejected(data_dir, tmp_path):
    token = cryptoBroker.encrypt("pw")
    # Simulate copying connections.json to a machine with a different key.
    os.remove(data_dir / cryptoBroker.KEY_FILE)
    cryptoBroker.reset_cache()
    assert cryptoBroker.decrypt(token) == ""


def test_unreadable_key_file_is_replaced(data_dir):
    (data_dir / cryptoBroker.KEY_FILE).write_bytes(b"not-a-valid-fernet-key")
    cryptoBroker.reset_cache()
    token = cryptoBroker.encrypt("pw")
    assert cryptoBroker.decrypt(token) == "pw"
