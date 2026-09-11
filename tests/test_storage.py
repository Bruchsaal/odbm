# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os

import pytest

from conftest import posix_only

import storage


def test_read_json_missing_returns_default(data_dir):
    assert storage.read_json("nope.json", default={"a": 1}) == {"a": 1}


def test_read_json_malformed_returns_default(data_dir):
    (data_dir / "bad.json").write_text("{not json")
    assert storage.read_json("bad.json", default={}) == {}


def test_write_then_read_round_trip(data_dir):
    storage.write_json("x.json", {"k": [1, 2, 3]})
    assert storage.read_json("x.json") == {"k": [1, 2, 3]}


@posix_only
def test_private_write_is_owner_only(data_dir):
    storage.write_json("secret.json", {"p": "x"}, private=True)
    mode = os.stat(data_dir / "secret.json").st_mode & 0o777
    assert mode == 0o600


def test_write_leaves_no_temp_files_behind(data_dir):
    storage.write_json("x.json", {"a": 1})
    leftovers = [p for p in os.listdir(data_dir) if p.startswith(".tmp-")]
    assert leftovers == []


def test_failed_write_preserves_previous_content(data_dir, monkeypatch):
    storage.write_json("x.json", {"good": True})

    # An unserializable payload blows up mid-write; the old file must survive.
    with pytest.raises(TypeError):
        storage.write_json("x.json", {"bad": object()})

    assert storage.read_json("x.json") == {"good": True}
    assert [p for p in os.listdir(data_dir) if p.startswith(".tmp-")] == []


def test_data_dir_is_isolated(data_dir):
    assert storage.get_data_dir() == str(data_dir)
    storage.write_json("here.json", {})
    assert (data_dir / "here.json").exists()


@posix_only
def test_non_private_files_are_not_owner_only(data_dir):
    """
    mkstemp creates 0600, so writing through the atomic path silently made
    queries.json and settings.json owner-only.
    """
    storage.write_json("queries.json", {"1": {}})
    mode = os.stat(data_dir / "queries.json").st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    assert mode == (0o666 & ~umask)
    assert mode != 0o600 or umask == 0o177


@posix_only
def test_private_and_public_writes_differ(data_dir):
    storage.write_json("public.json", {}, private=False)
    storage.write_json("secret.json", {}, private=True)
    pub = os.stat(data_dir / "public.json").st_mode & 0o777
    sec = os.stat(data_dir / "secret.json").st_mode & 0o777
    assert sec == 0o600
    assert pub != sec
