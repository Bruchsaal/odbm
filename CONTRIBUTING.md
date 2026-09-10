# Contributing to ODBM

Thanks for taking a look. Issues and pull requests are welcome.

## The one rule that matters: no AWR, no ASH

ODBM reads **only `v$` and `dba_` views**. It must never query:

- `dba_hist_*` (AWR)
- `v$active_session_history` (ASH)
- `dbms_workload_repository`, `dbms_sqltune`, `dbms_advisor` SQL Tuning APIs

Those are licensed features. Querying them requires the Oracle **Diagnostics
Pack** (~$7,500/processor) or **Tuning Pack** (~$5,000/processor), and Oracle
audits for exactly this. A single merged query touching them would silently
create a licence liability for **every** user of the tool — including people who
run Standard Edition and cannot buy the packs at all.

Being pack-free is the main reason this project exists. A PR that breaks it
cannot be merged, however useful the query is.

Almost anything worth knowing is available pack-free. If you are unsure whether
a view is gated, open an issue and ask before writing the code.

## Licensing of contributions

Contributions are accepted under **AGPL-3.0-or-later** — inbound equals
outbound. By opening a pull request you offer your change under that licence.

There is no CLA and no copyright assignment. The consequence is deliberate and
worth stating plainly: once your code is merged, the project **cannot** be
relicensed or dual-licensed commercially without your agreement. That is the
intended trade-off — it keeps ODBM open, permanently.

## Getting set up

```bash
python -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
python main.py           # http://127.0.0.1:9000
```

No Oracle database is needed: the test suite mocks the driver. Install `node` if
you want the JavaScript escaping tests to run rather than skip.

Run the app from a scratch directory rather than the checkout — `queries.json`
and `settings.json` are shipped defaults *and* runtime state.

## Adding or changing a query

Queries live in `queries.json` and are editable in the app's Library tab.
`tests/test_queries.py` enforces the properties that have regressed before, so
run it after any change. In particular:

- **Guard every division** with `NULLIF` — `ORA-01476` fires on an idle
  database, an unconfigured FRA, or `TOTALWORK = 0`.
- **Cap result sets** with `ROWNUM`, not `FETCH FIRST`: the latter is 12c+ only
  and the tool still supports 11g. The UI renders every row into the DOM.
- **Use `UNION ALL`** unless you genuinely want de-duplication; plain `UNION`
  silently collapsed equal values and under-reported a metric.
- **A query tagged with `historyName` must return exactly one row** — only the
  first row is recorded, so a multi-row query stores an arbitrary one.
- **No hardcoded credentials, passwords or absolute site paths.** Command
  templates use `<placeholders>`. This is enforced by the test suite.

## Pull requests

- Keep the diff focused; one concern per PR.
- Add or update a test for behaviour you change.
- CI runs the full suite on every push and pull request.
