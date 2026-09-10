# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import xml.etree.ElementTree as ET

import storage
from logBroker import logger

JSON_FILE = "queries.json"
XML_FILE = "sqlLib.xml"

# Fields the editor owns. Anything else on a query (refreshOptimum,
# historyName, future additions) is preserved untouched across a save.
EDITABLE_FIELDS = ("desc", "sql", "mode", "refreshOptimum", "historyName", "alert")

# Queries that fed the trend charts before history recording became a
# per-query property. Applied once so existing installs keep their graphs.
LEGACY_HISTORY_NAMES = {
    '1': 'Wait Classes',
    '2': 'Latency',
    '8': 'Used Space',
    '27': 'Active Sessions',
    '13': 'FRA',
    'PERF_04': 'Cache Hit Ratio',
    'AVAIL_02': 'Invalid Objects',
    'HIST_01': 'IO Throughput',
    'HIST_02': 'Workload Rate',
    'PERF_01': 'Active Session Count',
    'SPACE_01': 'Tablespace Usage %',
    'ASM_01': 'ASM Usage',
}


class QueryLibrary:
    def __init__(self):
        self.queries = {}
        self.load_library()

    def load_library(self):
        # try json first. it's faster.
        data = storage.read_json(JSON_FILE, default=None)
        if data is not None:
            if isinstance(data, dict):
                self.queries = data
                if self._backfill_history_names():
                    self.save_library()
                logger.info("Library", f"Loaded {len(self.queries)} queries from {JSON_FILE}")
                return
            logger.error(
                "Library",
                f"{JSON_FILE} is a {type(data).__name__}, expected an object",
                "Falling back to the bundled XML library.",
            )

        # A user-supplied sqlLib.xml in the data dir is an upgrade path from
        # very old versions; otherwise fall back to the bundled defaults.
        if os.path.exists(storage.data_file(XML_FILE)):
            self.migrate_xml()
        elif self._load_bundled_defaults():
            self.save_library()
        else:
            self.queries = {}

    def _xml_path(self):
        """Only a user-supplied XML counts; none is shipped any more."""
        candidate = storage.data_file(XML_FILE)
        return candidate if os.path.exists(candidate) else None

    def _load_bundled_defaults(self):
        """
        Load the pristine query library shipped inside the bundle.

        This is the factory default. The legacy sqlLib.xml is no longer
        shipped: it lagged the JSON library and carried site-specific
        statements, so restoring from it both lost queries and reintroduced
        content that must not ship.
        """
        path = storage.resource_path(JSON_FILE)
        if os.path.abspath(path) == os.path.abspath(storage.data_file(JSON_FILE)):
            return False  # dev mode: the working file is the default
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict) or not data:
            return False
        self.queries = data
        return True

    def _backfill_history_names(self):
        """Give pre-existing queries their history label. One-time upgrade."""
        changed = False
        already = any('historyName' in q for q in self.queries.values() if isinstance(q, dict))
        if already:
            return False
        for q_id, name in LEGACY_HISTORY_NAMES.items():
            entry = self.queries.get(q_id)
            if isinstance(entry, dict):
                entry['historyName'] = name
                changed = True
        if changed:
            logger.info("Library", "Tagged legacy queries for history recording")
        return changed

    def migrate_xml(self):
        logger.info("Library", "Migrating XML to JSON...")
        try:
            xml_path = self._xml_path()
            if not xml_path:
                self.queries = {}
                return
            tree = ET.parse(xml_path)
            root = tree.getroot()
            self.queries = {}

            for item in root.findall("sqlItem"):
                q_id = item.get("id")

                sql_node = item.find("sqltext")
                desc_node = item.find("description")
                box_node = item.find("boxline")

                sql = sql_node.text.strip() if (sql_node is not None and sql_node.text) else ""
                raw_desc = desc_node.text.strip() if (desc_node is not None and desc_node.text) else ""
                boxline = box_node.text.strip() if (box_node is not None and box_node.text) else ""

                # logic to determine mode from old descriptions
                mode = "GENERAL"
                final_desc = raw_desc

                if "CustomSQLtemplateQuery" in raw_desc:
                    mode = "QUERY"
                    if boxline:
                        final_desc = boxline
                elif "CustomSQLtemplateCommand" in raw_desc:
                    mode = "COMMAND"
                    if boxline:
                        final_desc = boxline

                if q_id:
                    entry = {
                        "id": str(q_id),
                        "desc": final_desc,
                        "sql": sql,
                        "mode": mode,
                    }
                    if str(q_id) in LEGACY_HISTORY_NAMES:
                        entry["historyName"] = LEGACY_HISTORY_NAMES[str(q_id)]
                    self.queries[str(q_id)] = entry

            self.save_library()
            logger.info("Library", f"Migration complete. {len(self.queries)} queries saved.")
        except (ET.ParseError, OSError) as e:
            logger.error("Library", "XML Migration failed", str(e))

    def save_library(self):
        try:
            storage.write_json(JSON_FILE, self.queries)
        except OSError as e:
            logger.error("Library", "Failed to save JSON", str(e))

    def get_query(self, query_id):
        return self.queries.get(str(query_id))

    def get_all_queries(self):
        # sort by id number if possible. else stick to bottom.
        return sorted(
            self.queries.values(),
            key=lambda x: int(x['id']) if str(x.get('id', '')).isdigit() else 9999,
        )

    def upsert_query(self, q_id, fields):
        """
        Merge editor changes into a query. Existing properties the editor did
        not send survive, so saving from the UI no longer strips metadata such
        as refreshOptimum.
        """
        q_id = str(q_id)
        entry = dict(self.queries.get(q_id) or {})
        entry["id"] = q_id
        entry.setdefault("mode", "GENERAL")

        for key in EDITABLE_FIELDS:
            if key in fields and fields[key] is not None:
                entry[key] = fields[key]

        self.queries[q_id] = entry
        self.save_library()
        return entry

    def delete_query(self, query_id):
        if str(query_id) in self.queries:
            del self.queries[str(query_id)]
            self.save_library()
            return True
        return False

    def reset_to_defaults(self):
        """Restore the shipped query library, discarding local edits."""
        self.queries = {}
        if self._load_bundled_defaults():
            self.save_library()
        elif self._xml_path():
            self.migrate_xml()
        return len(self.queries)

    # Retained so an older call site keeps working.
    reset_to_xml = reset_to_defaults
