# factory-blueprint — S2 Blueprint 与构建 / Blueprint & Build

Python implementation of the **S2 Blueprint & Build subsystem** of *DHIS2 App Studio
/ Metadata Factory*, as specified in
`../requirements/S2_Blueprint与构建_SRS.md` and constrained by
`../requirements/00_总体架构与集成约定.md` and the overall SRS.

S2 is the single semantic source of truth for a project and the only producer of
deterministic artifacts: it maintains App Blueprint versions, working drafts and
patches, judges change level and impact, allocates UIDs and codes, owns the Metadata
Registry (dispositions, controlled fields, environment bindings, managed scope),
compiles metadata packages, and runs the three local validation layers.

- Python 3.12+, SQLAlchemy 2.x (sync), Pydantic v2, Alembic, PostgreSQL schema `bp`.
- **Code version 0.2.0**, implementing SRS V1.1.5. `compiler_version` is `0.2.0`
  (the dependency graph gained degrees, orphans and unresolved references);
  `package_template_version` stays `0.1.0` because the development-package template
  did not change in this round.
- Public surface: **`factory_blueprint.api`** — the only module other subsystems import
  (00 doc 6.1, D-13). `api.__all__` is the contract, including the DTOs, enums and
  errors a caller needs to read results; SRS chapter 7 lists the same functions.
- Test doubles: **`factory_blueprint.testing`** — `FakeBlueprintApi` (in-memory, for S1
  and S3) and `BlueprintHarness` (real PostgreSQL, for S2's own tests).
- Depends on S0 (`factory_foundation`) for transactions, artifact storage, the audit
  hash chain, idempotency, identifiers, canonical JSON/hashing and progress or
  cancellation, and on S4 read-only services for UID generation and object existence.

S2 never calls an LLM, never writes to DHIS2, never calls S1 or S3, never reads the
clock or a random source while compiling, and never repairs an artifact — validation
reports and stops (overall SRS C-07).

## Public surface (0.2.0)

`api.__all__` is the whole contract; nothing else is imported by a caller. By SRS
chapter, the functions are:

| Chapter | Functions |
| --- | --- |
| 7.1 schema | `initialize`, `list_sections`, `list_object_types`, `get_section_schema` (also `section_schema`), `document_schema`, `current_schema_version`, `available_versions`, `migrate_document`, `register_migration`, `has_migration_path`, `prepare_initial_blueprint`, `register_initial_blueprint` |
| 7.2 versions | `get_current_version`, `list_versions`, `get_version`, `get_project_semantics`, `get_entries`, `locate_entries`, `get_decisions`, `diff_versions`, `diff_draft` |
| 7.3 drafts | `open_draft`, `prepare_draft_base`, `submit_patch`, `get_draft`, `get_active_draft`, `reset_draft`, `freeze_draft`, `promote_draft`, `prepare_mode_upgrade`, `commit_mode_upgrade` |
| 7.4 registry | `analyze_impact`, `get_registry`, `get_environment_bindings` / `get_bindings`, `get_object_relationships`, `get_managed_scope`, `replenish_uid_pool`, `get_uid_pool_status`, `set_environment_binding` |
| 7.5 build | `compile_baseline`, `compile_for_environment`, `get_package_manifest`, `validate_build`, `reproduce`, `query_issues`, `generate_frontend_package`, `get_dependency_graph`, `list_package_files`, `read_package_file` |
| 7.6 portability | `export_blueprint`, `prepare_import`, `commit_import`, `delete_project_data` |
| seam | `set_dhis2`, `get_dhis2`, `reset_providers`, `settings`, `configure`, `BlueprintSettings` |

The three product reads (`get_dependency_graph`, `list_package_files`,
`read_package_file`) are `@external_io` and answer only from frozen bytes. S1 must not
recompute graph nodes, degrees, orphans or dangling references, and must not unzip the
development package: S2 owns both formats (BP-TD-009, BP-HO-004, CP-UI-020).

## Where things live

| Path | What it holds |
| --- | --- |
| `api.py` | the whole public surface; nothing else is importable by contract |
| `readmodel.py` | the chapter 7.2 and 7.4 queries, plus the 7.5 `get_package_manifest`, and why two of them are external I/O |
| `artifact_reads.py` | the chapter 7.5 reads that answer from a product's bytes: a build's dependency graph (degrees, orphans, unresolved references, filters) and a development package's file listing and file content (BP-TD-009, BP-HO-004) |
| `build/` | compile, validate and reproduce orchestration; the build record that makes a product replayable (including `compile_parameters`) |
| `draft/service.py` | the draft life cycle: open → materialize → patch → freeze → promote, plus the delivery-mode upgrade's staged base |
| `patch/` | RFC 6902 application (add/replace/remove/move/copy/test), the five patch rules, the UID write guard, key index |
| `version/`, `impact/` | version numbering by change level, appendix A and appendix B |
| `registry/` | UID pool and allocation, code generation, bindings, managed scope, conflicts |
| `graph/` | the metadata object graph: field-level edges and the references that could not become edges |
| `compilers/` | metadata, baseline and environment-bound products, plus the M3 OpenAPI, UI Spec, Fixture and Package compilers |
| `capability/` | what one environment's Capability Profile says: version ranges, property verdicts, the Profile's own rules, and the differences it caused (FR-CAP-001/003) |
| `validators/` | the three layers, the offline DHIS2 expression parser, the cross-report issue read and 17.3's handoff checks |
| `content/`, `decisions/`, `schema/` | the Blueprint document/layout and per-section schemas, decision completeness, and schema version storage and migrations |
| `diff/`, `portability/` | object-level Diff, export, two-phase import, project cleanup |
| `s4/` | the read-only protocol S4 implements, its contract types, and today's stub |
| `models/`, `migrations/versions/` | `bp` tables and Alembic revisions `0001_bp_initial` → `0002_bp_prepared_base` → `0003_bp_compile_parameters` |
| `schemas/1/`, `descriptors/`, `validators/expressions/grammars/` | versioned *data*: section schemas, type descriptors and templates, expression grammars |
| `testing/` | `FakeBlueprintApi` and `BlueprintHarness` |

Adding an object type means adding a descriptor and a template.  Adding a DHIS2
version means adding a grammar file.  Neither touches a compiler or a validator
(BP-TD-001, BP-NFR-06).

`FakeBlueprintApi` answers the whole chapter 7 surface from dictionaries. For the 0.2.0
product reads it has four preset slots: `builds` (builds the Fake handed out),
`graphs` (one `DependencyGraph` per build id; empty means `NOT_GENERATED`), `packages`
(`{path: bytes}` per `FRONTEND_PACKAGE` artifact id) and `non_package_artifacts`
(artifact id → actual type, for `ARTIFACT_TYPE_MISMATCH`). It calls production's
`narrow_graph`, `package_file_kind`, `content_hash` and path guard rather than
reimplementing them, so a filtered graph or a package hash cannot differ between the
double and S2.

## How determinism is enforced, not promised

1. `CompileContext`/`CompileInputs` carries no clock, no random source, no session and
   no network handle, so a compiler cannot reach one even by accident.
2. Every collection is sorted before it is emitted, and JSON goes through S0's
   `file_json` / `structure_hash` only.
3. ZIP entries carry a fixed 1980 timestamp, `create_system=0` and `external_attr=0`.
4. Issue IDs are *derived* from rule, object, location and message, so the same
   problem gets the same ID in every run.
5. `tests/snapshots/products.json` pins the per-file hashes of one scenario's products,
   plus the public graph answers (`baselineGraph`, `environmentGraph`), so a read that
   disagrees with the bytes is caught in review rather than at integration; a change in
   shape has to be deliberate (`BP_SNAPSHOT_UPDATE=1` rerecords it).
6. `tests/unit/test_architecture.py` scans the pure packages for clock, random and
   network calls, checks that exactly the documented functions are `@external_io`,
   that `bp` declares no cross-schema foreign key, and that every emitted audit event
   and raised error code is registered.
7. An S4 probe result is stored in `bp.build_record.s4_probe` and never enters a
   product's bytes.
8. A rule ID that nothing emits is a check nobody performs: the guard test scans every
   `RULE_*` constant and fails if it is declared but never reported.  The `BPV-*` names
   are S2's own scheme — the SRS requires each Issue to carry a rule ID (BP-VAL-001)
   without naming them — and each one's comment cites the BP-/FR- rule it implements.

## Install and test

```bash
cd S2
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -e ".[dev]"     # S0 and S4 are editable path sources
                                                  # (../S0, ../S4); S4 carries the real
                                                  # read-only DHIS2 services

# unit tests only, no database needed
.venv/bin/pytest -q tests/unit

# integration tests against a local PostgreSQL — the cluster is the `factory-cp-pg`
# container, whose restart policy is `no`, so a host reboot leaves it stopped and the
# suite answers with setup errors rather than a missing database: `docker start factory-cp-pg`
export FACTORY_TEST_DATABASE_URL="postgresql+psycopg://factory:factory@localhost:55432/factory_test"
.venv/bin/pytest -q --cov=factory_blueprint
.venv/bin/ruff check src tests && .venv/bin/mypy
.venv/bin/python scripts/quality_gate.py          # BP-NFR-05: 90% in compilers/validators, 85% overall
```

`BlueprintHarness` rebuilds `core` and `bp` from scratch, so a failed run leaves no
state behind.  It only ever touches the database named by
`FACTORY_TEST_DATABASE_URL`.  Because it drops and recreates schemas, **two test
sessions cannot share that database at the same time** — use a separate database per
parallel run rather than a second `--cx-dist` style worker pool.

To rerecord the golden products on purpose (a product change that review accepted):

```bash
BP_SNAPSHOT_UPDATE=1 .venv/bin/pytest -q tests/snapshots   # writes products.json
.venv/bin/pytest -q tests/snapshots                        # now compares against it
```

The size limits of BP-NFR-01/02 (1,000 objects: compile < 30 s, validation < 30 s) are
guarded by a separate, opt-in marker, because they are slow rather than flaky:

```bash
FACTORY_PERF=1 .venv/bin/pytest -q -m perf tests/perf
```

The scenario compiles and validates 1,003 objects in about a second and a half in total.
The limit is easy to lose again: constructing a `BlueprintDocument` normalizes the whole
document, so building one inside a per-object loop costs seconds per thousand objects.
Both the compilers and the validators therefore build their document-wide maps once and
pass them down.

## Acceptance criteria and where each one is proved

| Criterion | Test |
| --- | --- |
| AC-BP-01 — every product traces to one version and hash | `test_promoted_version_artifact_is_traceable_to_its_hash`, `test_products_match_the_committed_snapshot` |
| AC-BP-02 — a frozen mapping needs an approved CR | `test_frozen_mapping_without_change_request_is_rejected`, `test_frozen_mapping_with_change_request_is_accepted` |
| AC-REG-01 — three compiles, same hashes, no new UID | `test_reproduce_is_byte_identical`, `test_products_survive_new_input_artifact_ids` |
| AC-REG-02 — a malformed or unregistered UID is BLOCKING | `test_blueprint_layer_blocks_a_malformed_or_unregistered_uid`, `test_uid_shaped_literal_in_page_reference_is_rejected` |
| AC-REG-03 — a reused OptionSet stays out of the payload | `test_organisation_unit_is_reference_only`, `test_registry_entry_requires_observation_for_reuse` |
| AC-REG-04 — UPDATE keeps the fields it does not own | `test_an_update_entry_writes_only_its_controlled_fields` |
| AC-COV-01 — a DataElement gets the environment's default CategoryCombo | `test_data_element_defaults_to_the_environment_category_combo` |
| AC-CAP-01 — the same version compiled against another Profile, and the difference shown | `test_ac_cap_01_a_different_version_is_compiled_with_its_own_profile`, `test_the_profile_is_recorded_by_version_and_hash_never_by_artifact_id`, `test_ac_cap_01_the_diff_ignores_what_the_instance_rewrites_on_its_own` |
| AC-ENV-01 — one version compiled to TEST and UAT: created UIDs identical, references resolved per environment | `test_ac_env_01_one_version_compiled_for_two_environments`, `test_ac_env_01_the_manifest_names_what_varies_by_environment` |
| AC-COV-02 — a missing translation is a WARNING, a present one ships | `test_static_layer_grades_a_missing_translation_as_a_warning`, `test_missing_and_present_translations_are_both_reported_honestly` |
| AC-VAL-01 — missing references, wrong types, unregistered objects | `test_static_layer_blocks_a_rule_reference_that_resolves_to_nothing`, `test_cross_artifact_layer_catches_the_inconsistency_dimensions` |
| AC-DATA-01 (S2 part) — recompile after an import is byte-identical | `test_recompiling_after_import_gives_the_same_bytes` |
| BP-AC-01 — a patch writing a UID is rejected | `test_patch_writing_uid_is_rejected` |
| BP-AC-02 — a patch outside the stage scope is rejected | `test_patch_outside_stage_scope_is_rejected` |
| BP-AC-03 — promoting with a stale hash is refused | `test_promote_with_a_stale_hash_is_rejected` |
| BP-AC-04 — a description-only change is Cosmetic | `test_cosmetic_only_change_bumps_the_revision` |
| BP-AC-05 — changing a Disposition is Major | `test_disposition_change_is_major` |
| BP-AC-06 — a weak decision without confirmation blocks freeze | `test_bp_ac_06_a_low_confidence_decision_without_confirmation_blocks_freeze` |
| BP-AC-07 — a retired UID is never handed out again | `test_bp_ac_07_a_retired_uid_is_never_handed_out_again`, `test_reset_discards_patches_and_retires_uids` |
| BP-AC-08 — two projects claiming one UID is a blocking conflict | `test_bp_ac_08_the_ownership_conflict_is_reported_and_not_fatal` |
| BP-AC-09 — a bad condition or unknown reference is BLOCKING | `test_static_layer_blocks_a_program_rule_condition_that_does_not_parse`, `test_an_unknown_key_is_reported_rather_than_shipped` |
| BP-AC-10 — a manual binding fixes the compile, no new version | `test_bp_ac_10_a_manual_binding_recompiles_without_a_new_version`, `test_bp_ac_10_a_manual_binding_to_an_absent_object_stays_blocking`, `test_set_manual_refuses_a_binding_that_cannot_mean_anything`, `test_set_manual_refuses_a_uid_another_project_manages` |
| BP-AC-11 — an empty pool refuses retryably | `test_bp_ac_11_a_dry_uid_pool_refuses_the_patch_retryably` |
| BP-AC-12 — one read spans a VALIDATION_REPORT and a DHIS2_IMPORT_REPORT, field structure identical, level named `level` | `test_both_report_kinds_answer_in_the_same_structure`, `test_an_import_row_gets_a_rule_id_a_source_stage_and_a_business_key` |
| BP-AC-13 — a binding carries `observedHash` and `discoveryResultId`, several environments in one call | `test_bindings_carry_the_observation_and_accept_several_environments`, `test_binding_fields_are_the_ones_a_drift_check_needs` |
| BP-AC-14 — two entries added and one changed read back that way | `test_diff_draft_reports_two_added_and_one_updated` |
| BP-AC-15 — the project's live draft, and empty when there is none | `test_get_active_draft_returns_id_base_stage_and_hash`, `test_get_active_draft_is_empty_until_a_draft_opens` |
| BP-SCH-008 — the section catalog: versions, containers, key fields, keyless sections | `test_list_sections_answers_with_versions_and_containers`, `test_whole_object_sections_are_marked_keyless` |
| BP-REG-008/009 against a **real** S4 — an UPDATE's own occupant is not a conflict | `test_an_update_entry_holding_its_own_uid_is_not_a_conflict`, `test_an_update_entry_held_by_another_type_is_blocked`, `test_an_update_entry_whose_code_is_its_own_is_not_a_conflict` |
| BP-ASM-03 — S2 parses the Profile and Discovery documents S4's own builders produce | `test_s2_reads_a_capability_profile_that_s4_wrote`, `test_s2_reads_a_discovery_result_that_s4_wrote`, `test_the_expression_grammar_s4_names_is_one_s2_ships` |
| AC-API-01 — a v2.41 Tracker read uses the new endpoint, a legacy one is BLOCKING | `test_a_legacy_tracker_path_is_blocked_even_though_it_resolves`, `test_the_tracker_route_a_profile_flags_is_accepted` |
| AC-API-02 — two pages needing the same list share one schema component | `test_two_identical_shapes_become_one_component`, `test_two_pages_sharing_a_shape_get_one_component_not_two_copies` |
| AC-API-03 — `baseUrl` servers, securitySchemes, per-operation Registry refs | `test_baseline_compile_emits_openapi_and_ui_specification`, `test_an_unbound_reference_keeps_the_baseline_placeholder` |
| AC-FE-01 — evidence present, package generated, pages FRONTEND_READY | `test_a_complete_package_marks_its_page_frontend_ready`, `test_generate_frontend_package_lays_out_the_17_1_tree` |
| AC-FE-02 — a ready page has spec, intents, mappings, examples, deployed deps | `test_a_page_without_a_ui_intent_is_not_ready`, `test_an_input_component_without_a_field_mapping_is_blocked`, `test_no_deployment_evidence_means_no_page_is_ready` |
| AC-FE-03 — no credential or real personal data; connection, example config and CORS are documented | `test_a_credential_looking_value_in_the_package_is_blocked`, `test_documenting_the_token_scheme_is_not_mistaken_for_a_credential`, `test_the_package_carries_no_credential_and_no_real_personal_data` |
| FR-POL-006/008, FR-DOM-001 — fixtures are synthetic, seeded and marked | `test_the_prefix_strategy_numbers_within_its_own_spec`, `test_a_sensitive_attribute_is_marked_in_the_value_itself`, `test_the_seed_decides_a_weighted_pick_and_a_different_seed_does_not` |
| BP-CMP-021/029 — env compile also emits the fixture package and the diff | `test_environment_compile_produces_a_seeded_synthetic_fixture_package` |
| BP-HO-002 — deployment evidence must describe *this* version | `test_evidence_for_another_blueprint_version_is_refused`, `test_production_evidence_alone_does_not_count_as_deployed_for_handoff` |
| BP-API-001 — the Fake covers the whole API | `test_the_fake_implements_every_public_function`, `test_a_caller_can_run_a_whole_design_cycle_without_a_database` |
| BP-AC-16 — a build's graph matches the package; degrees and orphans are the full graph's | `test_the_environment_bound_graph_matches_the_package_and_lists_dangling_refs`, `test_the_baseline_graph_is_readable_from_the_same_function`, `test_a_filtered_read_keeps_the_full_graph_degrees_and_orphans`, `test_the_graph_lists_orphans_and_dangling_references_separately` |
| BP-AC-17 — no graph yet is `NOT_FOUND` with `details.reason = NOT_GENERATED` | `test_a_build_that_produced_no_graph_is_not_generated`, `test_a_build_id_nobody_handed_out_is_not_exist`, `test_a_build_with_no_preset_graph_reports_not_generated` |
| BP-AC-18 — the package listing and one file of it, hashing the same | `test_the_package_listing_gives_path_kind_size_and_hash`, `test_a_read_file_hashes_as_the_listing_says`, `test_the_fake_lists_and_reads_preset_package_files` |
| BP-AC-19 — a commit package handed to the dev-package read is `ARTIFACT_TYPE_MISMATCH` | `test_a_commit_package_handed_to_the_package_read_is_a_type_mismatch` |
| BP-AC-20 — a crafted path answers exactly like an absent one | `test_a_crafted_path_is_indistinguishable_from_an_absent_one`, `test_an_artifact_that_does_not_exist_is_not_exist` |
| BP-CMP-021a — a built version's files, hashes and object counts; an unbuilt one is empty, not invented | `test_package_manifest_lists_the_files_a_build_produced`, `test_package_manifest_of_an_unbuilt_version_is_empty_not_invented` |
| BP-REG-010.2 — an UPDATE binds by UID only, never by a shared code | `test_bp_reg_010_bindings_resolve_by_code_then_uid_and_update_by_uid_only` |
| BP-VAL-020 架构 — the package is the approved version, and every object was mapped | `test_the_architecture_dimension_finds_the_approved_package_consistent`, `test_an_object_no_mapping_decision_produced_blocks_the_package`, `test_a_tracker_program_without_a_tracked_entity_type_blocks` |
| BP-NFR-01 / BP-NFR-02 — 1,000 objects compile and validate under 30 s | `test_compiling_a_thousand_objects_stays_under_thirty_seconds`, `test_validating_a_thousand_objects_stays_under_thirty_seconds` (`-m perf`) |
| FR-CMP-006, FR-COV-002…006 as pure rules | `tests/unit/test_compile_rules.py` — dispositions, UPDATE merging, dependencies, implicit CategoryCombo, sharing, translations, forbidden system fields |

`pytest -k "ac_bp or ac_reg or ac_cov or ac_val or bp_ac or ac_data"` runs the
criteria-named subset.


## Deviations from the SRS's API table, and why

* **`get_bindings` is published as both names.** Chapter 7.4 calls the read
  `get_environment_bindings`; S1's `adapters/registry.py` resolves `get_bindings`.  One
  function answers under both, so a caller written against either name works and neither
  has to be renamed to suit the other.
* **The package reads take `artifact_id`, not `package_artifact_id`.** S1 and S4 already
  use `package_artifact_id` for the `METADATA_PACKAGE` handed to S4 for a commit, while
  BP-HO-004 means the *development* package, and an artifact id is what both reads resolve.
  Chapter 7.5 carries this name since SRS V1.1.3.  The accepted types are one list
  (`artifact_reads.LISTABLE_PACKAGE_TYPES`, today `FRONTEND_PACKAGE` alone), so adding the
  metadata package at a later milestone changes no signature — and handing one to this read
  answers `ARTIFACT_TYPE_MISMATCH` rather than pretending nothing was generated.
* **`prepare_draft_base`** (chapter 7.3) has no row in the SRS table.  `open_draft` and
  `submit_patch` are transactional, and reading a version's content is external I/O
  that S0 forbids inside a transaction.  So the materialization step gets its own
  external-I/O call — the same reason the SRS itself gives for BP-DRF-008.
* **`get_entries` and `analyze_impact`** are marked 只读 in the table but are
  `@external_io`: they answer with entry *contents*, which live in the version
  artifact.  `locate_entries` is the transaction-safe half: same selection, positions
  only.
* **`observedUid`.** BP-DRF-005.4 says a patch may not write a UID, while REUSE,
  REFERENCE and UPDATE entries have to record which existing object they mean.  A patch
  therefore records an *observation* (`observedUid`); S2 promotes it to `uid` while
  applying the patch.  The guard stays absolute: no patch can set the identity field.
* **The UID ledger is append-only per concept.** `bp.uid_allocation` allows one *active*
  allocation per `(project_id, businessKey)` and keeps every retired row forever, so a
  concept a reset discarded can be planned again with a fresh UID (BP-AC-07) without
  ever reissuing an old number.
* **An observed UID is not an allocation.** REUSE, REFERENCE and UPDATE entries carry a
  number S2 did not issue, so they stay out of the ledger; a second project claiming
  the same object is reported as a BLOCKING ownership conflict at freeze, rather than
  aborting the patch with a key violation.
* **languages** live in `project` only.  BP-SCH-002 and BP-SCH-003 disagree about
  whether `generationPolicy` also carries them; BP-SCH-002 is the schema rule, so it
  wins, and `schemas/1/sections.json` says so in its description.
* **M1–M4 are delivered; M5 is not.** `generate_frontend_package`,
  `prepare_mode_upgrade` and `commit_mode_upgrade` run for real, and the manual binding
  path and per-Profile recompile are the M4 deliverables. The DataStore compiler
  (BP-CMP-045) is still held back and answers `BP_FEATURE_NOT_ENABLED` through
  `require_compiler`; the runnable starter project (BP-CMP-046) and reverse-building a
  Blueprint from existing metadata have no public entry point yet. Tier 2/3 descriptors
  are not enabled. Because gating is a settings check (`config.enabled_compilers`),
  enabling or holding back a compiler is a configuration change, not a restructuring
  (BP-TD-002).
* **A hand-set environment binding is a claim until a compile confirms it.** BP-REG-010.3
  puts the choice in a person's hands and BP-BR-14 keeps the version still, so S2 refuses
  the choices that cannot mean anything (no such Registry entry, not a UID, a CREATE
  object, another project's object) and reads the rest back against that environment's
  Discovery Result: the confirmation is what supplies `observedHash` and
  `discoveryResultId`, and a UID the instance does not have stays BLOCKING rather than
  becoming a dangling reference in a payload.
* **A version's own answers bind the compile, and the Diff says what they changed.**
  `capability/` is where the Profile is read: a descriptor's version range, the properties
  this version removed or deprecated, its schema for one type, its import defaults and the
  rules it states about S2's own output.  Nothing branches on a version string (FR-CAP-001),
  and every disagreement between the shipped descriptor and that environment is recorded in
  the DIFF_REPORT and in the package README, so a difference is attributable to the version
  instead of guessed at (AC-CAP-01, BP-CMP-030).
* **S2 and S4 leave the same fields out of a field-level Diff.** S4 excludes a Profile's
  volatile and forbidden properties when it compares object content (DX-DSC-007); S2 now
  does the same for the environment comparison, otherwise one deploy would answer "what
  changed" twice with two different lists.  The row's own `uid` and `dhis2Type` are
  identification, not content, so they are not compared either.
* **An observation baseline belongs to the environment it was observed in, and every
  watched object owes one.** A Registry entry records what the *design* environment showed
  at freeze time.  A package for TEST or UAT now carries that instance's own `observedHash`
  and `lastUpdated` from its Discovery Result, for each REUSE, REFERENCE **and UPDATE**
  object (BP-CMP-027a, AC-ENV-01) — S2 had been leaving UPDATE out of the section entirely,
  which made S4's drift check report `NO_BASELINE` for the one disposition a Brownfield
  deploy can overwrite.  Without this, S4 compares the object at one UID against content
  hashed at another (its join is by businessKey, its read is by UID:
  `execution/drift.py:32` and `:94`) and reports a change nobody made.
  `test_s4_interop.py` reads a real S2 package through S4's own `MetadataPackage` and
  `drift_watch`, so this seam is checked against S4's reader rather than against a fixture.
* **A referenced object is listed once.** Two objects sharing one implicit reference (the
  environment's default CategoryCombo) used to appear twice in
  `referencesOutsideManagedScope`, and an object that is both a Registry entry and an
  out-of-scope reference reached S4's watch list twice — no verdict changes, but a moment
  of observation gets counted twice in the fingerprint meant to identify it (S4's
  DX-EXE-029).  Both lists are keyed by `(businessKey, uid)` now, and the implicit
  reference row carries the baseline BP-CMP-027a asks of any reference.
* **A development package carries no environment base URL.** FR-API-005 fixes `servers`
  to a `baseUrl` **variable**, and S2 has no instance host to put in it — reading one
  from S4 would put an environment's address into a product that must recompile
  byte-identically elsewhere.  So the variable's default is `""`, meaning the instance
  that serves the app, and `dhis2/environment.example.json` shows where the real value
  goes.
* **`{baseUrl}` and the token scheme are documented, not embedded.** BP-HO-110 looks
  for credential-*shaped* values (`ApiToken <8+ chars>`, a `password:` assignment, a
  private key block).  `Authorization: ApiToken <token>` must appear, because
  FR-API-005 requires the package to describe the authentication modes.

## Known limits and tradeoffs (0.2.0)

* **`package_template_version` is still `0.1.0`.** The compiler version moved to `0.2.0`
  because the graph product changed, but the development-package template did not, so
  the two compatibility dimensions move independently (BP-NFR-08). A build written by
  0.1.0 will therefore report `consistent=False` from `reproduce()` — the correct answer
  for bytes a different compiler produced.
* **`ARTIFACT_TYPE_MISMATCH` is S0's code, not S2's.** S2 raises it but registers it
  only if the deployed S0 catalogue lacks it (`registries.S0_PENDING_ERROR_CODES`);
  once S0 ships FD-COM-001a, that entry must be deleted. S2 never overwrites an owner or
  retryability S0 declared.
* **Graph edges are field references only.** `edge_kind` exists in the model and product
  but only `ref` is produced today; `implicit`, `sharing` and `attribute` await the
  producers that would create them. Degrees, `orphans` and `unresolved_registry_refs`
  are computed once on the whole graph and only trimmed by a filter.
* **Export is version-centric.** It carries version contents, the UID ledger, the UID
  pool, bindings, build records and validation runs. Patches, drafts and the derived
  indexes (Registry, decisions, business keys, edges) are deliberately absent: the
  importing deployment rebuilds indexes from content (BP-VER-002). Import refuses to
  leave two promotable versions or to take a UID another project already owns.
* **Some reads are narrower than "one or many".** `get_registry` attaches one
  environment's UIDs; use `get_environment_bindings` for several. `get_decisions` returns
  all decisions of a version when given an empty id list and does not paginate.
* **UID pool policy is caller-supplied.** `replenish_uid_pool` takes batch size and
  low-water mark (defaults 200/50) rather than reading the project's
  `generationPolicy`; `get_uid_pool_status` reports the configured default mark. The
  pool is deleted whole by project cleanup, and there is no per-environment purge.
* **The DataStore product is not delivered.** An environment compile can write
  `datastore-changes.json` only if the document carries `appConfigurations`, and the
  current section schema has no such section; the DataStore compiler is not in
  `enabled_compilers`. Enabling it later is a settings and schema change, not a new
  code path in an existing compiler.
* **Project cleanup never deletes the UID ledger.** Active allocations become RETIRED,
  retired rows stay forever, and the database trigger refuses a DELETE; S0 artifact
  bytes are outside `bp` and are not removed by this call.

## Wiring S4 in for real

`src/factory_blueprint/s4/` holds the whole boundary: `protocol.py` (the four read-only
methods S2 needs), `contract.py` (Capability Profile and Discovery Result, tolerant of
field-name drift), `adapter.py` (the only file that imports `factory_dhis2`) and
`stub.py` (deterministic in-memory answers, for a test that wants to script them).

**The default is the real subsystem.** `deps.get_dhis2()` returns an `S4Adapter` over
`factory_dhis2.api` and nothing else unless a caller installs a double:

```python
from factory_blueprint import api
api.set_dhis2(StubDhis2())      # a test or the standalone Fake, on purpose
api.set_dhis2(None)             # back to the real S4
```

That inversion is deliberate. A silent fallback to invented UIDs, existence answers and
deletion verdicts is not a degraded mode, it is corrupted data: a pool full of numbers
nobody generated, and an ownership conflict that no environment was ever asked about.

Install S4 beside S2 with `uv pip install -e "../S4"` (declared as the `s4` extra,
because S1 and S3 install S2 without carrying S4). The import is deferred to the first
call, so S2 stays importable where S4 is absent; a call then fails with
`INTERNAL_ERROR` naming `factory_dhis2` rather than answering from nowhere.

Two shapes S4 publishes are load-bearing and are pinned by
`tests/integration/test_s4_interop.py`:

* **Occupancy, not existence.** `uid_exists` and `code_exists` answer with who holds the
  identifier. An UPDATE entry's UID is *supposed* to exist in the environment — that is
  what the disposition means — so a bare boolean would report every Brownfield update as
  a BP-REG-008 conflict. S2's protocol carries `UidOccupancy` / `CodeOccupancy` and only
  reports an occupant that is a different type, or a different object for the same code.
* **The expression grammar name.** S4 publishes `expressionGrammarVersion` as
  `dhis2-2.41`; S2's grammar files register that string as an alias, so the parser the
  local expression check uses is chosen by S4's version, not by an S2 guess (BP-VAL-021).

