# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Frontend regression tests.

app.js builds table markup as strings and injects it with x-html, so the
escaping helper is the only thing standing between a hostile value in a
monitored database and script execution in the dashboard.
"""
import json
import os
import re
import shutil
import subprocess
import textwrap

import pytest

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "app.js")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run_js(snippet):
    """Evaluate a snippet against the real esc() implementation from app.js."""
    source = open(APP_JS, encoding="utf-8").read()
    # Take everything before the Alpine bootstrap: the helpers, verbatim.
    helpers = source.split('document.addEventListener("alpine:init"')[0]
    program = helpers + textwrap.dedent(snippet)
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@node
@pytest.mark.parametrize("raw,expected", [
    ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
    ('" onmouseover="alert(1)', "&quot; onmouseover=&quot;alert(1)"),
    ("' onfocus='x", "&#39; onfocus=&#39;x"),
    ("a & b", "a &amp; b"),
    ("plain text", "plain text"),
])
def test_esc_neutralises_html(raw, expected):
    assert run_js(f"console.log(esc({json.dumps(raw)}));") == expected


@node
def test_esc_handles_non_strings():
    assert run_js("console.log(esc(42), esc(null), esc(undefined));") == "42 null undefined"


@node
def test_esc_escapes_ampersand_first():
    # Escaping & last would double-encode the entities produced by < and >.
    assert run_js("console.log(esc('&lt;'));") == "&amp;lt;"


@node
def test_app_js_is_syntactically_valid():
    result = subprocess.run(["node", "--check", APP_JS], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_table_value_is_escaped():
    """No raw ${val}/${c} interpolation may survive in the table renderer."""
    source = open(APP_JS, encoding="utf-8").read()
    renderer = source.split("render() {", 1)[1].split("this.html = s;", 1)[0]
    for forbidden in ("${val}", "${c}", "${data[0].error}"):
        assert forbidden not in renderer, f"unescaped interpolation: {forbidden}"


def test_sql_id_is_bound_not_concatenated():
    source = open(APP_JS, encoding="utf-8").read()
    assert 'binds: { sql_id: id }' in source
    assert '/^[A-Za-z0-9_]{1,32}$/.test(id)' in source


def test_save_connection_captures_index_before_reset():
    """Regression: cancelEdit() cleared editingIndex before it was compared."""
    source = open(APP_JS, encoding="utf-8").read()
    body = source.split("async saveConnection() {", 1)[1].split("},", 1)[0]
    assert "const targetIndex = this.editingIndex;" in body
    assert "if (this.editingIndex === this.selectedConnIndex)" not in body


# ------------------------------------------------------------------- footer

INDEX_HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "static", "index.html")


def _footer():
    html = open(INDEX_HTML, encoding="utf-8").read()
    start = html.index("ODBM Licenses")
    return html[start - 800:start + 1400]


def test_footer_links_to_licence_and_source():
    """AGPL section 13: network users must be offered the corresponding source."""
    footer = _footer()
    for href in ("/licenses/agpl", "/licenses/third-party",
                 "https://github.com/Bruchsaal/odbm"):
        assert href in footer, f"footer is missing {href}"


def test_source_url_is_consistent_everywhere():
    """
    AGPL section 13 makes this link an obligation, so the three places that
    state it must agree. Checked against COPYRIGHT rather than `git remote`:
    the canonical public URL must not depend on which checkout you are in
    (the private repo has a different remote).
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    copyright_text = open(os.path.join(root, "COPYRIGHT"), encoding="utf-8").read()
    match = re.search(r"Source code:\s*(\S+)", copyright_text)
    assert match, "COPYRIGHT does not declare a source URL"
    url = match.group(1).rstrip("/")

    for rel in ("static/index.html", "static/app.js"):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            assert url in f.read(), f"{rel} does not point at {url}"


def test_footer_reveal_is_keyboard_reachable():
    """A hover-only reveal would strand keyboard users on the hidden links."""
    footer = _footer()
    assert "group-hover:-translate-y-4" in footer
    assert "group-focus-within:-translate-y-4" in footer
