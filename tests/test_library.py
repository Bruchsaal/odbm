# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import storage
from libBroker import LEGACY_HISTORY_NAMES, QueryLibrary


def test_loads_from_json(seeded):
    lib = QueryLibrary()
    assert lib.get_query("1")["desc"] == "Wait Classes"
    assert len(lib.get_all_queries()) == 4


def test_editing_preserves_refresh_optimum(seeded):
    """Regression: saving from the UI used to wipe refreshOptimum."""
    lib = QueryLibrary()
    lib.upsert_query("1", {"desc": "edited", "sql": "select 9 from dual", "mode": "QUERY"})

    q = lib.get_query("1")
    assert q["refreshOptimum"] == 60
    assert q["desc"] == "edited"
    assert q["sql"] == "select 9 from dual"

    # And it survives a reload from disk, not just in memory.
    assert QueryLibrary().get_query("1")["refreshOptimum"] == 60


def test_editing_preserves_history_name(seeded):
    lib = QueryLibrary()
    lib.upsert_query("1", {"desc": "edited", "sql": "s", "mode": "QUERY"})
    assert lib.get_query("1")["historyName"] == "Wait Classes"


def test_refresh_optimum_can_be_changed(seeded):
    lib = QueryLibrary()
    lib.upsert_query("1", {"desc": "d", "sql": "s", "mode": "QUERY", "refreshOptimum": 15})
    assert lib.get_query("1")["refreshOptimum"] == 15


def test_history_name_can_be_set_and_changed(seeded):
    lib = QueryLibrary()
    lib.upsert_query("2", {"desc": "d", "sql": "s", "mode": "QUERY", "historyName": "New Metric"})
    assert lib.get_query("2")["historyName"] == "New Metric"


def test_none_fields_do_not_overwrite(seeded):
    # The API sends None for fields the editor omitted.
    lib = QueryLibrary()
    lib.upsert_query("1", {"desc": "d", "sql": "s", "mode": "QUERY",
                           "refreshOptimum": None, "historyName": None})
    q = lib.get_query("1")
    assert q["refreshOptimum"] == 60
    assert q["historyName"] == "Wait Classes"


def test_insert_new_query(seeded):
    lib = QueryLibrary()
    lib.upsert_query("99", {"desc": "new", "sql": "select 1", "mode": "QUERY"})
    assert lib.get_query("99") == {"id": "99", "mode": "QUERY", "desc": "new", "sql": "select 1"}


def test_delete(seeded):
    lib = QueryLibrary()
    assert lib.delete_query("1") is True
    assert lib.get_query("1") is None
    assert lib.delete_query("1") is False


def test_sorting_puts_numeric_ids_first(seeded):
    ids = [q["id"] for q in QueryLibrary().get_all_queries()]
    assert ids == ["1", "2", "14", "CMD_1"]


def test_list_shaped_json_recovers_instead_of_crashing(data_dir, no_bundled_library):
    """A bad bootstrap once wrote [] here, and get_query() then raised."""
    storage.write_json("queries.json", [])
    lib = QueryLibrary()
    assert lib.get_query("1") is None       # used to be AttributeError
    assert lib.get_all_queries() == []


def test_malformed_json_does_not_crash(data_dir, no_bundled_library):
    (data_dir / "queries.json").write_text("{{{")
    assert QueryLibrary().get_all_queries() == []


def test_unusable_json_falls_back_to_bundled_defaults(data_dir):
    """A broken local queries.json recovers from the bundled default library."""
    storage.write_json("queries.json", [])
    lib = QueryLibrary()
    assert len(lib.get_all_queries()) > 0
    assert isinstance(lib.get_query("1"), dict)


def test_no_library_at_all_is_empty_not_fatal(data_dir, no_bundled_library):
    lib = QueryLibrary()
    assert lib.get_all_queries() == []
    assert lib.get_query("anything") is None


def test_history_names_backfilled_for_legacy_library(data_dir, no_bundled_library):
    storage.write_json("queries.json", {
        "1": {"id": "1", "desc": "Wait Classes", "sql": "s", "mode": "QUERY"},
        "27": {"id": "27", "desc": "AAS", "sql": "s", "mode": "QUERY"},
        "50": {"id": "50", "desc": "Not a metric", "sql": "s", "mode": "QUERY"},
    })
    lib = QueryLibrary()
    assert lib.get_query("1")["historyName"] == LEGACY_HISTORY_NAMES["1"]
    assert lib.get_query("27")["historyName"] == LEGACY_HISTORY_NAMES["27"]
    assert "historyName" not in lib.get_query("50")
    # Persisted so it only runs once.
    assert "historyName" in storage.read_json("queries.json")["1"]


def test_backfill_does_not_override_user_choices(data_dir, no_bundled_library):
    storage.write_json("queries.json", {
        "1": {"id": "1", "desc": "d", "sql": "s", "mode": "QUERY", "historyName": "Mine"},
        "27": {"id": "27", "desc": "d", "sql": "s", "mode": "QUERY"},
    })
    lib = QueryLibrary()
    assert lib.get_query("1")["historyName"] == "Mine"
    # Library already migrated, so 27 is left alone rather than re-tagged.
    assert "historyName" not in lib.get_query("27")


def test_xml_migration_when_no_json(data_dir, no_bundled_library):
    (data_dir / "sqlLib.xml").write_text(
        '<root><sqlItem id="1"><description>CustomSQLtemplateQuery</description>'
        '<boxline>Wait Classes</boxline><sqltext>select 1 from dual</sqltext></sqlItem>'
        '<sqlItem id="42"><description>Plain</description><sqltext>select 2</sqltext></sqlItem>'
        '</root>'
    )
    lib = QueryLibrary()
    assert lib.get_query("1")["mode"] == "QUERY"
    assert lib.get_query("1")["desc"] == "Wait Classes"
    assert lib.get_query("1")["historyName"] == "Wait Classes"
    assert lib.get_query("42")["mode"] == "GENERAL"
    assert storage.read_json("queries.json")  # written out as JSON


def test_reset_falls_back_to_a_user_supplied_xml(data_dir, no_bundled_library):
    (data_dir / "sqlLib.xml").write_text(
        '<root><sqlItem id="7"><description>D</description><sqltext>select 7</sqltext></sqlItem></root>'
    )
    storage.write_json("queries.json", {"99": {"id": "99", "desc": "x", "sql": "s", "mode": "QUERY"}})
    lib = QueryLibrary()
    lib.reset_to_defaults()
    assert lib.get_query("99") is None
    assert lib.get_query("7")["sql"] == "select 7"



# ------------------------------------------------------- factory reset source

def test_reset_restores_the_bundled_library(data_dir, tmp_path):
    """
    Reset used to re-parse sqlLib.xml, which lagged queries.json by 19 entries
    and carried site-specific statements — so a reset both lost curated
    queries and reintroduced content that must never ship.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    default = {str(i): {"id": str(i), "desc": f"q{i}", "sql": "select 1", "mode": "QUERY"}
               for i in range(1, 6)}
    (bundle / "queries.json").write_text(json.dumps(default))
    storage.set_resource_dir(str(bundle))
    try:
        storage.write_json("queries.json", {"99": {"id": "99", "desc": "local",
                                                   "sql": "s", "mode": "QUERY"}})
        lib = QueryLibrary()
        assert lib.get_query("99") is not None

        assert lib.reset_to_defaults() == 5
        assert lib.get_query("99") is None
        assert len(lib.get_all_queries()) == 5
        assert len(storage.read_json("queries.json")) == 5
    finally:
        storage.set_resource_dir(None)


def test_reset_prefers_bundled_defaults_over_a_stale_xml(data_dir, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "queries.json").write_text(json.dumps(
        {"1": {"id": "1", "desc": "from bundle", "sql": "select 1", "mode": "QUERY"}}))
    (data_dir / "sqlLib.xml").write_text(
        '<root><sqlItem id="1"><description>from xml</description>'
        '<sqltext>select 2</sqltext></sqlItem></root>')
    storage.set_resource_dir(str(bundle))
    try:
        lib = QueryLibrary()
        lib.reset_to_defaults()
        assert lib.get_query("1")["desc"] == "from bundle"
    finally:
        storage.set_resource_dir(None)


def test_no_xml_library_is_shipped():
    """sqlLib.xml contained another site's credentials; it is no longer shipped."""
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    assert not _os.path.exists(_os.path.join(root, "sqlLib.xml"))
    workflow = open(_os.path.join(root, ".github/workflows/release.yml"), encoding="utf-8").read()
    assert "sqlLib.xml" not in workflow
