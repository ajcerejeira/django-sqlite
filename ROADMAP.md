# django-sqlite — feature roadmap & scratchpad

Status: planning only. Nothing here is implemented; this is a working
document to scope what "first-class SQLite support for Django" could mean
and in what order to build it. Package is currently `Development Status ::
1 - Planning` with only an `AppConfig` and a sketch of a `sqlar` model — this
doc is the plan for turning that into something publishable.

## Premise

Django's SQLite backend works, but treats SQLite as a fallback for tests and
toy projects rather than a production target. Meanwhile SQLite itself has
grown features (WAL, STRICT tables, FTS5, R*Tree, JSON1, generated columns,
`sqlar`, `VACUUM INTO`, session/changeset extensions, mmap I/O) that make it
viable for real small-to-medium deployments, especially paired with
Litestream/LiteFS for replication. `django-sqlite` should be the layer that
exposes those features through idiomatic Django APIs: backend/connection
config, schema editor hooks, migration operations, model fields, querysets,
management commands, system checks, and storage backends.

Non-goal: this is not GeoDjango-lite or an ORM replacement. Every feature
should be additive and opt-in — a project not using FTS5 or `sqlar` should
be unaffected.

## Prior art / things to check before building

- Django's own SQLite backend already improves every release. Before
  building anything, re-check the target Django version's
  `django.db.backends.sqlite3` for what's already covered (JSON1 via
  `JSONField`, `GeneratedField` since 5.0, `transaction_mode` OPTIONS since
  5.1, expression/partial indexes, constraints with conditions). The value
  of this package is the *delta*, not reimplementing what core already does.
- Small existing ecosystem to survey for naming/API conventions and to
  avoid duplicating: `django-sqlite-fts` variants, `dj-database-url`
  sqlite options, `django-litestream`-style snippets, `sqlite-utils` (Simon
  Willison) for CLI conventions, `apsw` for an alternative driver.
- Confirm which SQLite features require a newer libsqlite3 than what ships
  with the target Python's stdlib `sqlite3` (FTS5, RTREE, and JSON1 are
  often compile-time optional). `pysqlite3-binary` is the usual escape
  hatch — decide whether it's an optional dependency or just documented.

## Feature catalog

Each feature below with its main Django integration points and rough risk.

### 1. Connection / pragma management (low risk, high value)

- A `SQLITE_PRAGMAS` (or similar) setting: a dict of pragmas applied on
  every new connection via the `connection_created` signal — the correct
  place, since pragmas like `foreign_keys`, `synchronous`, `cache_size`,
  `temp_store`, `mmap_size`, `busy_timeout` are per-connection, while
  `journal_mode=WAL` is persisted at the file level (only needs setting
  once, but harmless to repeat).
- Sensible production preset vs. Django's current defaults (which favor
  test-suite speed): WAL journal mode, `synchronous=NORMAL`,
  `foreign_keys=ON`, a non-zero `busy_timeout` so concurrent writers block
  briefly instead of raising `database is locked` immediately.
- Optional: a thin `DatabaseWrapper`/backend module
  (`ENGINE = "django_sqlite.backend"`) that wraps
  `django.db.backends.sqlite3` and applies the preset automatically, for
  people who don't want to wire the signal themselves. Should stay a
  drop-in superset, not a fork.
- Touchpoints: `signals.py` (connection_created receiver), `conf.py` (app
  settings + validation), optional `backend/base.py`.

### 2. System checks (low risk, high leverage for a "planning"-stage pkg)

- Register Django `checks` (`W`/`E` codes) that inspect the active
  connection/settings and warn about footguns:
  - `journal_mode` not WAL outside of tests
  - `foreign_keys` pragma off
  - `check_same_thread=True` combined with a threaded WSGI server
  - `synchronous=FULL` on a write-heavy config (informational, not an error)
  - SQLite compiled without FTS5/RTREE/JSON1 when the corresponding
    django_sqlite feature is used in the project
  - Litestream-incompatible settings (e.g. journal_mode not WAL, or `VACUUM`
    scheduled without a Litestream-aware snapshot) — see §8
- This is cheap to build, easy to test, and immediately demonstrates value
  even before anything else ships — good candidate for the first release.
- Touchpoints: `checks.py`, registered in `apps.py` via `register()`.

### 3. STRICT tables

- SQLite's `STRICT` table option enforces column types instead of the
  classic type-affinity free-for-all. Django never emits it.
- Needs a custom `SchemaEditor` (subclassing the sqlite3 one) that appends
  `STRICT` to `CREATE TABLE` when a model opts in (e.g. via a `Meta` option
  or an app-level default), plus a migration operation to convert an
  existing table (SQLite can't `ALTER TABLE ... STRICT`; requires the
  create-new-table/copy-rows/drop/rename dance Django's SQLite schema
  editor already does internally for other unsupported ALTERs).
- Risk: medium. Table-rebuild migrations are the trickiest part of the
  SQLite schema editor to get right (FK handling, indexes, triggers all
  need re-creating). Worth scoping as its own phase with thorough tests.
- Touchpoints: `backend/schema.py`, a migration `Operation` subclass.

### 4. Full-text search (FTS5)

- FTS5 tables are virtual tables (`CREATE VIRTUAL TABLE ... USING
  fts5(...)`), which Django's migration framework has no native concept of.
  Needs:
  - A migration operation to create/drop the virtual table.
  - An "external content" pattern (FTS5 table referencing a normal content
    table by rowid) plus generated triggers to keep the index in sync on
    insert/update/delete — this is the standard FTS5 idiom and avoids
    duplicating data.
  - A queryset/manager API: `.search("query")` compiling to `... MATCH %s`,
    plus a way to order by `rank`/`bm25()`.
  - Tokenizer configuration (unicode61 vs porter vs trigram) as a model
    option.
- This is the highest-value, highest-complexity feature — most likely to
  be *the* reason someone reaches for this package (search without
  Postgres/Elasticsearch). Scope as a distinct subpackage (`fts/`).
- Touchpoints: `fts/models.py`, `fts/lookups.py`, `fts/operations.py`.

### 5. R*Tree spatial indexes

- SQLite's `rtree` module gives bounding-box spatial indexing without
  GDAL/GEOS — a lightweight alternative to GeoDjango for "nearby point"
  queries (store min/max x/y per row, query by bounding-box intersection).
- Same virtual-table problem as FTS5: needs a migration operation and a
  queryset helper for bounding-box filters.
- Lower priority than FTS5 — smaller audience, but relatively contained
  once the FTS5 virtual-table migration machinery exists (they can likely
  share plumbing for "manage a virtual table via migrations").
- Touchpoints: `rtree/models.py`, `rtree/lookups.py`, `rtree/operations.py`.

### 6. `sqlar` (SQLite Archive) storage backend

- There's already a start at `src/django_sqlite/sqlar/models.py`. Two gaps
  to fix before building on it:
  - The real `sqlar` schema is
    `name TEXT PRIMARY KEY, mode INT, mtime INT, sz INT, data BLOB` — the
    current sketch has a field named `model` (should be `mode`, the Unix
    file-permission bits) and `name` isn't marked as the primary key.
    Matching the upstream schema exactly matters if we want interop with
    the `sqlite3` CLI's `.archive`/`-A` support, `sqlite3_archive`, and
    tools like DB Browser for SQLite that recognize the `sqlar` table by
    name and shape.
  - Model should probably be `managed = False` with the table created via
    a migration operation using the exact upstream DDL, rather than letting
    Django's schema editor generate its own column types/constraints.
- The actual feature on top: a `django.core.files.storage.Storage`
  subclass backed by the `sqlar` table (zlib-compressed blobs, like the
  reference implementation), usable as `DEFAULT_FILE_STORAGE` or a
  per-`FileField` `storage=`. Pitch: single-file deployment — models *and*
  media in one `.sqlite3` file, trivially backed up/copied/replicated.
- Management commands to import a directory tree into an archive and
  export it back out, ideally byte-compatible with files produced by the
  `sqlite3` CLI's own archive support.
- Touchpoints: `sqlar/models.py` (fix), `sqlar/storage.py`,
  `sqlar/management/commands/sqlar_import.py`,
  `sqlar/management/commands/sqlar_export.py`.

### 7. Maintenance management commands

- `sqlite_info` — report pragma values, `PRAGMA compile_options`, page
  size/count, freelist count, file size, journal mode: a "is my DB
  healthy/production-ready" snapshot.
- `sqlite_optimize` — run `PRAGMA optimize` (+ optionally `ANALYZE`).
- `sqlite_integrity_check` — `PRAGMA integrity_check` / `quick_check`,
  non-zero exit on failure (cron/CI-friendly).
- `sqlite_backup` — hot backup via `VACUUM INTO` (or the sqlite3 backup
  API) to a target path; safe to run against a live WAL-mode database.
- Touchpoints: `management/commands/*.py`.

### 8. Concurrency / locking ergonomics

- SQLite allows one writer at a time; under load, `OperationalError:
  database is locked` is the standard failure mode when `busy_timeout`
  isn't enough. A small retry-with-backoff decorator/context manager for
  write transactions (distinct from `busy_timeout`, which blocks at the
  SQLite level — this is an application-level fallback for when even that
  times out) covers the common case without requiring a message queue.
- Possibly pair this with docs (not code) on when to reach for a
  write-serialization queue instead — this package shouldn't try to be a
  job queue.
- Touchpoints: `concurrency.py` (decorator/context manager).

### 9. Multi-database / `ATTACH` helpers

- Common SQLite pattern: one file per tenant, or archiving old data into a
  separate file while still being able to query it. Django's multi-db
  routing already supports "many sqlite files," but doesn't help with
  `ATTACH DATABASE` for cross-file joins in raw SQL, or with ergonomically
  provisioning a new tenant file + running migrations against it.
- Scope as helper router base classes + a context manager for
  attach/detach around raw queries, rather than trying to make the ORM
  itself attach-aware (that's a much bigger, riskier feature).
- Lower priority — validate real demand before investing here.

### 10. Litestream / backup-tool integration

- Mostly documentation rather than code: what settings make a Django+
  SQLite deployment Litestream-safe (WAL mode, no `VACUUM` outside of
  Litestream-aware windows, `synchronous` recommendation), plus maybe a
  system check (see §2) that flags configurations known to fight with
  continuous replication.

### 11. Testing utilities (for consumers of this package, and for its own test suite)

- `django_sqlite.test` helpers: a pragma-asserting helper
  (`assertPragma(conn, "journal_mode", "wal")`), a `TestCase` mixin for
  FTS5/RTree fixtures, and guidance on Django's own per-thread/in-memory
  test-db behavior (relevant because pragmas are per-connection and
  Django's test runner may reuse/rebuild connections in ways that surprise
  people).

### 12. Admin integration (nice-to-have, do last)

- Read-only "database info" admin page (reuses §7's `sqlite_info` logic).
- Admin actions to trigger `optimize`/`integrity_check` from the UI for
  small deployments without shell access.

## Proposed module/file structure

```
src/django_sqlite/
    __init__.py
    apps.py                      # existing; also register checks here
    conf.py                      # app settings (SQLITE_PRAGMAS, feature flags) + validation
    checks.py                    # §2 system checks
    signals.py                   # §1 connection_created pragma application
    concurrency.py               # §8 retry-on-locked helper
    backend/                     # §1/§3 optional drop-in ENGINE
        __init__.py
        base.py                  # DatabaseWrapper wrapping django.db.backends.sqlite3
        schema.py                # SchemaEditor: STRICT tables, virtual-table DDL helpers
        features.py
    fts/                         # §4
        __init__.py
        models.py                # Fts5-backed model base / mixin
        lookups.py               # MATCH lookup, rank/bm25 ordering
        operations.py            # CreateFts5Table / sync-trigger migration operations
    rtree/                       # §5
        __init__.py
        models.py
        lookups.py
        operations.py
    sqlar/                       # §6 (models.py already exists, needs fixing)
        __init__.py
        models.py
        storage.py               # Storage backend
        management/
            commands/
                sqlar_import.py
                sqlar_export.py
    management/
        commands/
            sqlite_info.py        # §7
            sqlite_optimize.py
            sqlite_integrity_check.py
            sqlite_backup.py
    contrib/
        admin.py                  # §12
        test.py                   # §11 pytest/TestCase helpers
```

Everything under `fts/`, `rtree/`, `sqlar/`, `backend/`, `contrib/` should
be importable independently — a project using only `sqlar` storage
shouldn't need to think about FTS5, and vice versa. `apps.py` should only
wire up things that are unconditionally cheap (checks, signals); virtual
table features stay opt-in per-model.

## Phased roadmap (suggested order)

1. **Foundation** — test infra (pytest-django, a `tests/` package, a CI
   test job — currently `ci.yml` only lints/type-checks), `conf.py` +
   `signals.py` pragma management, `checks.py`. Small surface, immediate
   value, de-risks the rest.
2. **`sqlar` v1** — fix the existing model to match upstream schema,
   ship the storage backend + import/export commands. Concrete, visible
   "single-file Django app" story for a README demo.
3. **Maintenance commands** (§7) — small, independent, easy wins.
4. **STRICT tables** (§3) — exercises the hard part of the SQLite schema
   editor (table rebuilds) before FTS5 needs similar machinery.
5. **FTS5** (§4) — the flagship feature; budget the most time/review here.
6. **R*Tree, multi-db/ATTACH, admin UI, Litestream docs** (§5, §9, §10,
   §12) — opportunistic, based on interest after the above ships.

## Examples to write (for README + Sphinx docs)

- `settings.py` snippet: enabling pragma preset (signal-based and
  backend-based versions), with before/after on `journal_mode`.
- A tiny "blog post search" walkthrough using the FTS5 model + `.search()`.
- A "nearby locations" walkthrough using the R*Tree model.
- Single-file deployment demo: models + `sqlar` storage for `MEDIA`, one
  `.sqlite3` file, `sqlite_backup` in a cron/systemd-timer snippet,
  Litestream config pointer.
- `sqlar_import`/`sqlar_export` CLI usage against an existing directory.
- System check output: what a `manage.py check` run looks like with a
  deliberately bad pragma config, and after fixing it.
- Retry-on-locked decorator wrapping a view or a Celery task.
- Multi-tenant `ATTACH`-based cross-file query example.
- A "verifying STRICT enforcement" example (attempt to insert a wrong-typed
  value, show the `IntegrityError`/`OperationalError`).

## Tests to write

- **Pragmas/signals**: connection to a temp-file DB applies every
  configured pragma; verify via `PRAGMA <name>` readback, not just "no
  exception." Cover both the signal-only and backend-wrapper paths.
- **Checks**: for each registered check, a settings/config combination that
  should trigger it and one that shouldn't (standard Django
  `SimpleTestCase` + `checks.run_checks()` pattern).
- **STRICT tables**: migration produces `STRICT` in `sqlite_master.sql`;
  inserting a mistyped value raises; a table-rebuild migration (STRICT
  toggled on an existing table with data, FKs, and an index) preserves
  rows/FKs/indexes.
- **FTS5**: virtual table created via migration; insert/update/delete on
  the content table keeps the FTS index in sync (via triggers);
  `.search()` returns expected matches; rank ordering behaves; behavior
  when SQLite is compiled without FTS5 (skip with a clear message, not a
  cryptic `OperationalError`).
- **R*Tree**: virtual table CRUD; bounding-box intersection correctness
  including edge cases (touching boxes, containment, no overlap).
- **`sqlar` storage**: full Django `Storage` API contract (save, open,
  exists, delete, size, url where applicable) — Django's own test suite
  for storage backends is a good template to adapt; round-trip
  compression; large-file handling; `sqlar_import`/`sqlar_export`
  round-trip against `tmp_path` fixtures, and ideally against a real
  `.sqlar` file produced by the `sqlite3` CLI for interop.
- **Management commands**: `call_command` tests for each of `sqlite_info`,
  `sqlite_optimize`, `sqlite_integrity_check`, `sqlite_backup` — check
  output/exit codes; for `sqlite_backup`, assert the produced file is a
  valid, openable SQLite DB with matching row counts.
- **Concurrency**: simulate `sqlite3.OperationalError("database is
  locked")` and assert the retry helper retries the configured number of
  times with backoff, then re-raises.
- **Compile-option gating**: a shared test helper that checks `PRAGMA
  compile_options`/module availability up front and skips
  FTS5/RTree-dependent tests with a clear reason on interpreters where
  they're unavailable (relevant on some manylinux/macOS stdlib builds).
- **Matrix coverage**: run the suite across supported Python (3.10–3.14
  per current classifiers) and Django (5.2+ per current dependency) — a
  `tox.ini`/`nox` config feeding the same matrix into CI, replacing/
  extending the current lint-only `ci.yml`.

## Packaging / PyPI checklist

- Add a real test job to `.github/workflows/ci.yml` (today it only lints
  with ruff and type-checks with ty — no tests run at all yet).
- Add `pytest`, `pytest-django` (and maybe `tox`/`nox`) to the `dev`
  dependency group in `pyproject.toml`.
- Add a `tests/` package with a minimal Django settings module for the
  test suite.
- Coverage reporting (e.g. `coverage.py` + a badge) once tests exist —
  README already has doc/CI/license/ruff/ty badges, a coverage + PyPI
  version badge are the natural next additions.
- A `CHANGELOG` (Keep a Changelog style pairs well with the existing
  `setuptools-scm` version-from-git-tag setup).
- Bump `Development Status` classifier as milestones land (`2 - Pre-Alpha`
  once the foundation phase ships, `3 - Alpha` once `sqlar`+checks are
  documented and tested, `4 - Beta` once FTS5 ships).
- A release workflow: tag-triggered GitHub Action that builds (`uv build`
  or `python -m build`) and publishes to PyPI, ideally via PyPI's Trusted
  Publisher (OIDC) flow rather than a stored token.
- `py.typed` marker file (package already opts into `Typing :: Typed`
  classifier — confirm the marker file exists once real code lands, so
  type checkers pick up the shipped annotations).
- Decide and document the minimum SQLite version supported (drives which
  features degrade gracefully vs. hard-require, e.g. FTS5/STRICT need
  fairly recent SQLite).

## Open questions (for you, not derivable from the code)

- Naming: single `SQLITE_` settings namespace (e.g. `SQLITE_PRAGMAS`,
  `SQLITE_FTS_DEFAULT_TOKENIZER`) vs. a nested `DJANGO_SQLITE = {...}`
  dict — worth deciding early since it's an API surface that's annoying to
  rename later.
- Whether `backend/` (a wrapping `ENGINE`) is worth the maintenance cost
  vs. just shipping the `signals.py` approach as the primary path and
  documenting manual `OPTIONS["init_command"]` as the escape hatch —
  the wrapper is nicer UX but is one more thing to keep in sync with
  Django's own SQLite backend across versions.
- Whether `pysqlite3-binary` should be an optional extra
  (`django-sqlite[fts]` pulling it in) for environments whose stdlib
  `sqlite3` lacks FTS5/RTREE, or purely a documentation note.
- How much of GeoDjango's API shape to mirror for the R*Tree feature (a
  `BBoxField`-style API would feel familiar to anyone who's used
  GeoDjango) vs. keeping it deliberately minimal.
- Whether admin integration (§12) is in scope at all for v1, or explicitly
  deferred — it's the least essential feature relative to effort.
