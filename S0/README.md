# factory-foundation (S0 公共基础库 / Foundation)

Python implementation of the **S0 Foundation subsystem** for *DHIS2 App Studio /
Metadata Factory*, per `../requirements/S0_公共基础库_SRS.md` (SRS V1.0.2).
Package version: **0.1.2** (`pyproject.toml` / `CHANGELOG.md`).

S0 is the single shared dependency for subsystems S1–S4 and depends on none of
them (依赖无环, D-17). It delivers: database connection & unit-of-work, artifact
storage, the append-only audit hash chain, idempotency store, object-store &
secret adapters, common tools (errors / ids / canonical JSON & hashing / clock /
operation context / structured logging, tracing & redaction / config), the
progress & cancellation protocols, a per-schema migration runner, and a test suite.

- Python 3.12+, SQLAlchemy 2.x (sync), Pydantic v2, Alembic, PostgreSQL schema `core`.
- Public surface: **`factory_foundation.api`** (the only module other subsystems import).
- Test doubles & fixtures: **`factory_foundation.testing`**.

## Capabilities

| Area | What is implemented (0.1.2) |
| --- | --- |
| Database / transaction | `unit_of_work` (nested joins, outermost commit, rollback), `after_commit`, `@external_io` guard, `fk_check` cross-schema FK scanner (dotted targets across per-schema `MetaData`), `optimistic` version helpers, ordered per-schema migration runner (`core→dx→bp→ai→cp`, explicit dev-only downgrade) |
| Artifacts | two-phase staging + registration with read-back hash verification, immutable versions & supersede, classification floor, query/lineage/version history, purge/retention, diff, pack/unpack (unpack re-reads every written object before registering and removes its own writes on failure) |
| Audit | append-only row + truncate guards, global hash chain linked after commit, range/full verification, credential redaction, event-type registry, query |
| Idempotency | `(scope, key)` records with request digest, replay/in-progress/conflict, non-expiring `dx.execution` |
| Object stores | `memory://`, `file://`/`local://`, `s3://` (plus `http(s)://` endpoints); `key_from_location` restores the key from a stored location for every backend, including prefixed S3 and absolute `file://` roots |
| Secrets | `vault://`, `gcp://`/`aws://` (injectable cloud/vault transports), `env://` and `memory://`; `Secret` masked string form; env backend and development wiring refused under `APP_ENV=production`; writes to read-only backends report `SECRET_STORE_UNSUPPORTED` |
| Common tools | error catalog (31 S0-owned built-ins, owner/retryability, registry-conflict on code or ID-prefix reassignment), prefixed ULIDs, canonical/file JSON + `sha256:` hashing, system/fixed clock, operation context, structured JSON logging with automatic redaction, optional OpenTelemetry tracing, settings loader |
| Progress | `ProgressReporter` / `CancellationToken` protocols, null/recording reporters, manual token |
| Testing | `FoundationHarness` (stands up all five schemas, rebuilds `core`), pytest fixture, memory object store / memory secret backend / `FixedClock` re-export, testcontainers fallback |
| Housekeeping | `run_housekeeping()`: expired staging, pending object-deletion retry, expired idempotency records |

## Layout

```
src/factory_foundation/
  api.py               # single public entry point (00 doc 6.1, D-13)
  enums.py             # DataClassification, ArtifactStatus, OperatorType, ...
  bootstrap.py         # initialize_foundation(), CORE_MIGRATION_CONFIG
  housekeeping.py      # run_housekeeping(): staging, pending deletions, idempotency
  common/              # FD-COM-001..008: errors, ids, hashing, clock, context,
                       # logging, tracing, redaction, config
  db/                  # FD-DB-001..007: engine, per-schema bases, uow, external_io,
                       # optimistic, fk_check, migrations
  models/              # core schema ORM models + per-schema declarative bases
  artifacts/           # FD-ART-001..015: catalog, service, diff, packing, dto
  audit/               # FD-AUD-001..010: service, chain, registry, dto
  idempotency/         # FD-IDEM-001..003
  objectstore/         # FD-OBJ-001..002: memory, local, s3, provider, key_from_location
  secrets/             # FD-SEC-001..004: env, memory, vault, cloud, resolver
  progress/            # FD-PRG-001..002: ProgressReporter / CancellationToken
  migrations/          # Alembic environment for `core`
  testing/             # FD-TST-001: harness, fakes, fixtures
```

## Install

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"        # add ".[s3]" for boto3, ".[otel]" for OpenTelemetry
```

## Tests

The suite runs against a real PostgreSQL (the audit hash chain, append-only
triggers and migrations are exercised end-to-end). Point it at a database via
`FACTORY_TEST_DATABASE_URL`, e.g.:

```bash
export FACTORY_TEST_DATABASE_URL="postgresql+psycopg://factory:factory@localhost:5432/factory_test"
.venv/bin/pytest --cov=factory_foundation
```

Each test gets a freshly created `core` schema (dropped + re-migrated by the
`foundation` fixture) plus the `cp`/`bp`/`ai`/`dx` schemas stood up empty for
subsystem migrations (FD-TST-001), a memory object store, a fixed clock and a
memory secret backend.  Without `FACTORY_TEST_DATABASE_URL` the harness starts a
PostgreSQL test container: install the `testing` extra for that
(`factory-foundation[testing]`, or `[dev]`).

Quality gates (all part of the deliverable):

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy            # strict mode
.venv/bin/pytest -q --cov=factory_foundation   # 192 tests, 91% lines (FD-NFR-05 >= 90%)
```

## Acceptance criteria mapped to tests

| AC | Behaviour | Test |
| --- | --- | --- |
| FD-AC-01 | content hash mismatch → not registered | `test_artifacts.py::test_stage_hash_mismatch_aborts` |
| FD-AC-02 | audit failure rolls back business in same UoW | `test_audit.py::test_audit_failure_rolls_back_business` |
| FD-AC-03 | external I/O inside UoW → `TRANSACTION_MISUSE` | `test_db_uow.py::test_external_io_inside_uow_raises` |
| FD-AC-04 | derived classification below source without redaction → rejected | `test_artifacts.py::test_classification_downgrade_rejected` |
| FD-AC-05 | non-owner subsystem → rejected | `test_artifacts.py::test_non_owner_cannot_create` |
| FD-AC-06 | same key, different digest → `IDEMPOTENCY_CONFLICT` | `test_idempotency.py::test_same_key_different_digest_conflict` |
| FD-AC-07 | Authorization header masked & flagged redacted | `test_audit.py::test_credential_redaction` |
| FD-AC-08 | tampered archive → whole unpack fails | `test_artifacts.py::test_pack_unpack_roundtrip_and_hash_verify` |
| FD-AC-09 | cross-schema FK → checker fails | `test_db_uow.py::test_cross_schema_fk_detected_and_raises` |
| AC-SEC-01 | tampered audit record → break reported | `test_audit.py::test_verify_detects_tampering` |

## Key invariants (enforced)

- Artifact content is immutable and hash-verified on read-back before registration (FD-ART-003/004); unpack re-reads every object it writes before registering anything (FD-BR-02).
- Object-store `content_location` values round-trip back to keys (`key_from_location`), including absolute `file://` roots and prefixed `s3://` locations (FD-OBJ-001).
- Audit records are append-only at the database layer (row guard + truncate guard, FD-AUD-003) and chained by `hash = SHA-256(prevHash ‖ canonical(record))` (FD-AUD-004).
- Cross-schema foreign keys are forbidden and detectable, including dotted targets declared against a different schema's `MetaData` (D-08, FD-DB-006).
- External-I/O functions refuse to run inside an open transaction (FD-DB-004).
- Secret values are wrapped so their string form is always masked; the env backend and development wiring fail closed in production (FD-SEC-001/002).
- Error codes and ID prefixes have a single owner; reassignment raises `REGISTRY_CONFLICT` (FD-COM-001a/002).
- Migrations run in order `core → dx → bp → ai → cp`; production is forward-only and a downgrade must name an explicit target in a non-production environment (FD-DB-007).

## Known limitations (0.1.2)

Tracked in `CHANGELOG.md` ("仍待裁决/仍开放") and `../requirements/S0_公共基础库_SRS.md`
§11: most of the 31 built-in error codes are registered but not yet raised (coarse codes
remain); `ENVIRONMENT_LOCKED` ownership is still contested between S1 and S4; the archive
manifest is not authenticated and `pack`/`unpack` buffer the whole archive; `artifact` lacks
a `(prev_version_id, version)` unique constraint; optimistic concurrency is caller-enforced;
S0's own retention/staging cleanup performs object-store I/O inside a short transaction;
`dataclasses.asdict(secret)` exposes the value.
