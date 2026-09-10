# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Guards on the shipped SQL library.

These run with no database: they parse queries.json and assert the properties
that previously regressed. Anything needing a real instance is out of scope
here and listed in the plan's verification section instead.
"""
import json
import os
import re

import pytest

import widgetBroker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "queries.json"), encoding="utf-8") as f:
    QUERIES = json.load(f)

ALL = sorted(QUERIES.items(), key=lambda kv: (0, int(kv[0])) if kv[0].isdigit() else (1, kv[0]))
IDS = [k for k, _ in ALL]


def strip_literals(sql):
    """Remove comments, string literals and quoted aliases before pattern matching."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'[^']*'", "''", sql)
    sql = re.sub(r'"[^"]*"', '""', sql)
    return sql


def outer_query(sql, _strip=True):
    """
    Return the statement with subquery bodies replaced by a SUBQ token, leaving
    the outermost SELECT list intact.

    Only parenthesised groups whose body starts with SELECT are removed, so
    function calls survive with their names — ROUND(SUM(x)) must still read as
    an aggregate, while a GROUP BY nested in a FROM subquery must not.
    """
    if _strip:
        sql = strip_literals(sql)
    out = []
    i, n = 0, len(sql)
    while i < n:
        if sql[i] != "(":
            out.append(sql[i])
            i += 1
            continue
        depth, j = 0, i
        while j < n:
            if sql[j] == "(":
                depth += 1
            elif sql[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= n:                      # unbalanced; leave the rest as-is
            out.append(sql[i:])
            break
        body = sql[i + 1:j]
        if re.match(r"(?i)^\s*select\b", body.strip()):
            out.append(" SUBQ ")
        else:
            out.append("(" + outer_query(body, _strip=False) + ")")
        i = j + 1
    return "".join(out)


# ------------------------------------------------------------- shipped content

# The shipped library once carried a real account, its password and a couple of
# site-specific profile names, copied in from a customer install. These match the
# *shape* of that mistake rather than the specific strings, so the guard keeps
# working for content nobody has written yet.
SECRET_SHAPES = [
    # ALTER USER x IDENTIFIED BY <literal> - a credential, not a template
    (r"IDENTIFIED\s+BY\s+(?!<)[\"']?\w", "hardcoded credential"),
    # password/passwd assigned a literal value
    (r"\b(?:password|passwd)\s*(?:=|=>)\s*(?!<)[\"']?\w", "hardcoded password"),
    # absolute filesystem paths tie a query to one specific host
    (r"'/(?:u\d+|opt|oradata|home)/", "absolute site path"),
]

PLACEHOLDER_OK = re.compile(r"<[^>]+>")


def mask_placeholders(sql):
    """
    Blank out <placeholders> before scanning.

    Masking rather than exempting the whole statement: a template can still
    carry a hardcoded site path, which is exactly how
    ALTER DATABASE DATAFILE '/u07/oradata/<Instance>/...' slipped through.
    """
    return PLACEHOLDER_OK.sub(" ", sql)


@pytest.mark.parametrize("pattern,label", SECRET_SHAPES, ids=[s[1] for s in SECRET_SHAPES])
def test_no_secret_shaped_content_in_the_library(pattern, label):
    offenders = [qid for qid, q in ALL
                 if re.search(pattern, mask_placeholders(q["sql"]), re.I)]
    assert not offenders, f"{label} in queries {offenders}"


def test_command_templates_use_placeholders():
    """
    Every COMMAND entry is a fill-in-the-blank template. One that names a real
    user, profile or path is a copied-in statement that should not ship.
    """
    for qid, q in ALL:
        if q["mode"] != "COMMAND":
            continue
        if re.match(r"^\s*ALTER\s+(SYSTEM|DATABASE)\b", q["sql"], re.I):
            continue  # instance-wide settings take no site-specific identifier
        assert PLACEHOLDER_OK.search(q["sql"]), \
            f"[{qid}] COMMAND template has no <placeholder>: {q['sql'][:70]}"


@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_no_trailing_semicolon(qid, q):
    """A trailing ';' through the driver raises ORA-00911."""
    assert not q["sql"].rstrip().endswith(";")


@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_no_sqlplus_only_syntax(qid, q):
    """SQL*Plus directives are not SQL and can only raise ORA-00900."""
    assert not re.match(r"^\s*(archive\s+log|column\s|set\s|spool\s|show\s)",
                        q["sql"], re.I)


# --------------------------------------------------------------- correctness

@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_division_by_a_column_is_guarded(qid, q):
    """Unguarded division by a column raises ORA-01476 on ordinary data."""
    sql = strip_literals(q["sql"])
    divisions = re.findall(r"/\s*([A-Za-z_][A-Za-z_0-9.]*)", sql)
    if not divisions:
        return
    assert re.search(r"NULLIF|DECODE\s*\(", sql, re.I), \
        f"[{qid}] divides by {divisions} with no NULLIF/DECODE guard"


@pytest.mark.parametrize("qid", ["1", "3", "27"])
def test_union_all_not_union(qid):
    """UNION de-duplicates equal AAS rows and silently under-reports the total."""
    sql = strip_literals(QUERIES[qid]["sql"])
    assert not re.search(r"\bUNION\b(?!\s+ALL)", sql, re.I)


def test_wait_class_query_is_instance_agnostic():
    """INST_ID = 1 was hardcoded, so RAC node 2 reported node 1's numbers."""
    sql = QUERIES["1"]["sql"]
    assert "INST_ID" not in sql.upper()


def test_rman_backup_query_does_not_call_incrementals_full():
    q = QUERIES["17"]
    assert "%INC%" not in q["sql"]
    assert "DB FULL" in q["sql"]


def test_top_sql_window_matches_its_description():
    """Query 10 said '24 hours' but filtered SYSDATE-1/24, i.e. one hour."""
    q = QUERIES["10"]
    assert "24 hours" in q["desc"]
    assert "SYSDATE - 1/24" not in q["sql"].replace(" ", " ")
    assert "SYSDATE-1/24" not in q["sql"].replace(" ", "")


@pytest.mark.parametrize("qid", ["10", "11", "98"])
def test_no_unused_rank_window(qid):
    """RANK() was computed and never selected — a full sort of v$sql for nothing."""
    assert "RANK" not in QUERIES[qid]["sql"].upper()


@pytest.mark.parametrize("qid", ["10", "11", "98"])
def test_schema_filter_does_not_use_like_sys(qid):
    """NOT LIKE '%SYS%' also excluded legitimate schemas such as SYSADM."""
    assert "%SYS%" not in QUERIES[qid]["sql"]


def test_session_list_includes_cpu_bound_sessions():
    """seconds_in_wait > 0 hid sessions actively burning CPU."""
    sql = strip_literals(QUERIES["20"]["sql"])
    assert not re.search(r"seconds_in_wait\s*>\s*0", sql, re.I)


def test_object_errors_use_dba_not_user_view():
    assert "dba_errors" in QUERIES["94"]["sql"].lower()
    assert "user_errors" not in QUERIES["94"]["sql"].lower()


def test_cache_hit_ratio_is_interval_based():
    """The v$sysstat form is cumulative since startup and plots as a flat line."""
    sql = QUERIES["PERF_04"]["sql"].lower()
    assert "v$sysmetric" in sql
    assert "v$sysstat" not in sql


# ------------------------------------------------------- history integration

HISTORY_TAGGED = [(k, q) for k, q in ALL if q.get("historyName")]


@pytest.mark.parametrize("qid,q", HISTORY_TAGGED, ids=[k for k, _ in HISTORY_TAGGED])
def test_history_queries_return_exactly_one_row(qid, q):
    """
    historyBroker.record() only stores rows[0], so a multi-row query silently
    records one arbitrary row. Every tagged query must be single-row.
    """
    outer = outer_query(q["sql"])
    aggregated = re.search(r"\b(COUNT|SUM|MAX|MIN|AVG)\s*\(", outer, re.I) \
        and not re.search(r"\bGROUP\s+BY\b", outer, re.I)
    from_dual = re.search(r"\bFROM\s+DUAL\b", outer, re.I)
    single_row_view = re.search(r"v\$(recovery_file_dest|sysmetric)\b", outer, re.I)
    assert aggregated or from_dual or single_row_view, \
        f"[{qid}] is tagged for history but may return multiple rows"


@pytest.mark.parametrize("qid,q", HISTORY_TAGGED, ids=[k for k, _ in HISTORY_TAGGED])
def test_history_columns_are_named(qid, q):
    """An unaliased COUNT(*) became a graph series literally called 'COUNT(*)'."""
    assert not re.search(r"COUNT\s*\(\s*\*\s*\)(?!\s+AS)", q["sql"], re.I)


def test_recorded_series_names_are_preserved():
    """Renaming a series orphans existing history, so pin the known set."""
    names = {q["historyName"] for _, q in HISTORY_TAGGED}
    assert {"Tablespace Usage %", "ASM Usage", "Wait Classes", "Active Sessions"} <= names


# ----------------------------------------------------------------- resources

# Word-bounded so v$sql does not also match v$sqltext, which is inherently
# bounded by the sql_id it is filtered on.
UNBOUNDED_SOURCES = re.compile(
    r"\b(v\$sql|dba_objects|dba_segments|v\$diag_alert_ext|dba_users|"
    r"v\$rman_backup_job_details|dba_errors|v\$locked_object|v\$session_longops)\b")


@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_large_result_sets_are_capped(qid, q):
    """app.js renders every row into the DOM; uncapped queries hang the browser."""
    sql = q["sql"].lower()
    if not UNBOUNDED_SOURCES.search(sql):
        return
    if re.search(r"\b(count|sum|max|min|avg)\s*\(", sql) and "group by" not in sql:
        return  # single-row aggregate
    assert "fetch first" in sql or "rownum" in sql, f"[{qid}] has no row cap"


def test_alert_log_query_is_bounded():
    """v$diag_alert_ext parses the XML alert log off disk — the priciest query."""
    sql = QUERIES["30"]["sql"].lower()
    assert "rownum" in sql or "fetch first" in sql
    assert "message_level" in sql
    assert "interval '7' day" not in sql


@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_row_caps_are_portable(qid, q):
    """
    FETCH FIRST is 12c+ syntax and raises ORA-00933 on 11g, while the container
    detection deliberately still supports 11g. ROWNUM works on every version.
    """
    assert "FETCH FIRST" not in q["sql"].upper()


# ------------------------------------------------------------ collection

def test_every_tracked_query_is_polled_by_the_collector():
    """
    History only accrues for queries something actually runs. Collection is
    server-side now, so the collector - not the browser - must pick up every
    query tagged with a historyName or an alert rule.
    """
    import collectorBroker

    class _StubLibrary:
        @staticmethod
        def get_all_queries():
            return list(QUERIES.values())

    c = collectorBroker.Collector()
    c.attach_library(_StubLibrary())
    tracked = {q["id"] for q in c._tracked_queries()}

    expected = {k for k, q in QUERIES.items() if q.get("historyName") or q.get("alert")}
    assert expected, "nothing is tagged for history or alerting"
    assert expected <= tracked, f"not polled: {sorted(expected - tracked)}"


def test_browser_no_longer_writes_history():
    """
    Recording used to be a side effect of the browser polling /api/metric,
    so closing the tab silently stopped it. The UI reads only now.
    """
    app_js = open(os.path.join(ROOT, "static/app.js"), encoding="utf-8").read()
    assert "history=false" in app_js
    assert "history=${record}" not in app_js


# ------------------------------------------------------------------ metadata

@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_mode_is_normalised(qid, q):
    """The editor dropdown only offers QUERY/COMMAND; GENERAL silently reclassified."""
    assert q["mode"] in ("QUERY", "COMMAND")


@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_mode_matches_the_statement(qid, q):
    is_ddl = re.match(r"^\s*(alter|create|drop|grant|revoke|truncate|insert|update|delete)\b",
                      q["sql"], re.I)
    assert q["mode"] == ("COMMAND" if is_ddl else "QUERY")


@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_refresh_optimum_is_non_negative(qid, q):
    assert q.get("refreshOptimum", 0) >= 0


@pytest.mark.parametrize("qid,q", ALL, ids=IDS)
def test_every_query_has_id_desc_and_sql(qid, q):
    assert q["id"] == qid
    assert q["desc"].strip()
    assert q["sql"].strip()


def test_widget_defaults_all_resolve():
    """A default pointing at a deleted query leaves a permanently empty widget."""
    missing = {k: v for k, v in widgetBroker.DEFAULT_SETTINGS.items() if v not in QUERIES}
    assert not missing


def test_sql_id_lookups_use_bind_variables():
    for qid in ("14", "15"):
        assert ":sql_id" in QUERIES[qid]["sql"]
        assert "sql_id=''" not in QUERIES[qid]["sql"].replace(" ", "")


def test_no_hardcoded_locale():
    """Query 80 pinned German NLS_NUMERIC_CHARACTERS."""
    assert "NLS_NUMERIC_CHARACTERS" not in json.dumps(QUERIES)


# ------------------------------------------------------------- licence files

LICENSE_PATH = os.path.join(ROOT, "LICENSE")
NOTICES_PATH = os.path.join(ROOT, "THIRD-PARTY-NOTICES.md")


def test_license_is_the_real_agpl():
    """The file previously named a standard licence but held a paraphrase of it."""
    text = open(LICENSE_PATH, encoding="utf-8").read()
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 19 November 2007" in text
    assert "13. Remote Network Interaction" in text
    assert "END OF TERMS AND CONDITIONS" in text
    assert len(text.split()) > 5000, "licence text looks truncated"


def test_license_has_no_unfilled_placeholder():
    assert "[YOUR NAME" not in open(LICENSE_PATH, encoding="utf-8").read()


def test_copyright_file_names_a_holder():
    text = open(os.path.join(ROOT, "COPYRIGHT"), encoding="utf-8").read()
    assert re.search(r"Copyright \(C\) \d{4}\s+\S+", text)
    assert "AGPL" in text or "Affero" in text


@pytest.mark.parametrize("component", [
    "Alpine.js", "Tailwind CSS", "ApexCharts", "svg.js",
    "python-oracledb", "certifi", "PyInstaller",
])
def test_third_party_notices_cover_every_bundled_component(component):
    assert component in open(NOTICES_PATH, encoding="utf-8").read()


def test_stripped_mit_notices_are_restored():
    """
    The vendored Alpine build carries no copyright at all, so MIT's notice
    requirement is only met by reproducing it here.
    """
    text = open(NOTICES_PATH, encoding="utf-8").read()
    assert "Caleb Porzio" in text
    assert "Permission is hereby granted, free of charge" in text
    assert "The above copyright notice and this permission notice" in text


def test_oracle_apache_notice_is_reproduced():
    """Apache-2.0 section 4(d) requires the bundled NOTICE to travel along."""
    assert "Copyright (c) 2016, 2025, Oracle and/or its affiliates." in \
        open(NOTICES_PATH, encoding="utf-8").read()


@pytest.mark.parametrize("doc", ["LICENSE", "THIRD-PARTY-NOTICES.md", "COPYRIGHT"])
def test_licence_documents_are_bundled_into_the_release(doc):
    workflow = open(os.path.join(ROOT, ".github/workflows/release.yml"), encoding="utf-8").read()
    assert f'--add-data "{doc}' in workflow


def test_credentials_are_never_bundled():
    """The release used to bake connections.json into every binary."""
    workflow = open(os.path.join(ROOT, ".github/workflows/release.yml"), encoding="utf-8").read()
    bundled = re.findall(r'--add-data\s+"([^"$]+)', workflow)
    assert not any("connections" in b for b in bundled), bundled
    assert not any("odbm_key" in b for b in bundled), bundled


@pytest.mark.parametrize("src", [
    "main.py", "dbBroker.py", "configBroker.py", "cryptoBroker.py",
    "storage.py", "libBroker.py", "historyBroker.py", "widgetBroker.py",
    "logBroker.py",
])
def test_source_files_carry_an_spdx_header(src):
    head = open(os.path.join(ROOT, src), encoding="utf-8").read(400)
    assert "SPDX-License-Identifier: AGPL-3.0-or-later" in head


def test_frontend_carries_a_license_banner():
    head = open(os.path.join(ROOT, "static/app.js"), encoding="utf-8").read(400)
    assert "SPDX-License-Identifier: AGPL-3.0-or-later" in head


# ---------------------------------------------------- pseudonymous authorship

SHIPPED_SOURCES = sorted(
    [os.path.join(ROOT, f) for f in os.listdir(ROOT) if f.endswith(".py")]
    + [os.path.join(ROOT, p) for p in (
        "COPYRIGHT", "README.md", "THIRD-PARTY-NOTICES.md",
        "static/app.js", "static/index.html", "queries.json",
        ".github/workflows/release.yml")]
)


HOLDER = "Bruchsaal"

# Files that legitimately carry other people's copyright.
THIRD_PARTY = ("THIRD-PARTY-NOTICES.md", "LICENSE")

COPYRIGHT_LINE = re.compile(r"Copyright\s+\(c\)\s*(\d{4}(?:\s*[-,]\s*\d{4})?)?\s*([^\n<]{0,60})", re.I)


def test_only_one_copyright_holder_in_first_party_files():
    """
    Asserted positively, on purpose.

    A deny-list of the names to keep out would have to spell those names in the
    repo - which is self-defeating for a project authored pseudonymously. This
    says who the holder *is* instead, so any other name fails without this file
    ever naming one.
    """
    offenders = []
    for path in SHIPPED_SOURCES:
        if not os.path.exists(path) or os.path.basename(path) in THIRD_PARTY:
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for _, holder in COPYRIGHT_LINE.findall(text):
            holder = holder.strip().rstrip(".").strip()
            if holder and not holder.startswith(HOLDER):
                offenders.append(f"{os.path.relpath(path, ROOT)}: {holder!r}")
    assert not offenders, f"unexpected copyright holder(s): {offenders}"


def test_every_first_party_source_declares_the_holder():
    missing = []
    for path in SHIPPED_SOURCES:
        if not path.endswith(".py"):
            continue
        with open(path, encoding="utf-8") as f:
            if f"Copyright (C) 2025 {HOLDER}" not in f.read(400):
                missing.append(os.path.relpath(path, ROOT))
    assert not missing, f"no copyright header: {missing}"


def test_copyright_holder_is_the_pseudonym():
    text = open(os.path.join(ROOT, "COPYRIGHT"), encoding="utf-8").read()
    assert "Copyright (C) 2025 Bruchsaal" in text


@pytest.mark.parametrize("src", [
    "main.py", "dbBroker.py", "configBroker.py", "cryptoBroker.py",
    "storage.py", "libBroker.py", "historyBroker.py", "widgetBroker.py",
    "logBroker.py", "download_assets.py",
])
def test_headers_name_the_pseudonym(src):
    head = open(os.path.join(ROOT, src), encoding="utf-8").read(300)
    assert "Copyright (C) 2025 Bruchsaal" in head


# ------------------------------------------------- everything that gets shipped

def _bundled_data_files():
    """Files the release workflow packs into the executable."""
    workflow = open(os.path.join(ROOT, ".github/workflows/release.yml"), encoding="utf-8").read()
    names = re.findall(r'--add-data\s+"([^"$]+)\$', workflow)
    paths = []
    for name in names:
        p = os.path.join(ROOT, name)
        if os.path.isdir(p):
            for dirpath, _, filenames in os.walk(p):
                paths.extend(os.path.join(dirpath, f) for f in filenames)
        elif os.path.exists(p):
            paths.append(p)
    return paths


@pytest.mark.parametrize("pattern,label", SECRET_SHAPES, ids=[s[1] for s in SECRET_SHAPES])
def test_no_bundled_file_contains_secrets(pattern, label):
    """
    Scan every artifact the workflow bundles, not just the obvious one:
    queries.json was cleaned once while sqlLib.xml, which also shipped, was not.
    """
    offenders = []
    for path in _bundled_data_files():
        if os.path.basename(path) in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
            continue  # licence prose, not configuration
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            if re.search(pattern, mask_placeholders(line), re.I):
                offenders.append(f"{os.path.relpath(path, ROOT)}: {line.strip()[:60]}")
                break
    assert not offenders, f"{label} ships in {offenders}"


def test_bundled_file_list_is_not_empty():
    """Guards the scan above against silently matching nothing."""
    assert len(_bundled_data_files()) > 3
