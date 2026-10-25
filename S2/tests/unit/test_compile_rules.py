"""Compiler rules that need no database (BP-CMP-040, FR-CMP-001…007, FR-COV-002…006).

``compile_metadata`` is a pure function of a Blueprint document plus the bindings and
observed content the caller hands it, so every branch that decides whether an object
ships — and what it says — is testable here.  The integration tests cover the wiring;
this file covers the rules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from factory_blueprint.compilers import files, metadata
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.dto.build import BuildContext
from factory_blueprint.dto.issue import Issue
from factory_blueprint.enums import Disposition, IssueLevel
from factory_blueprint.registry import scope as managed_scope
from factory_blueprint.s4 import contract
from factory_blueprint.s4.stub import discovered_object, discovery_document, profile_document
from tests.unit import helpers

PROJECT = "p1"
VERSION = "0.1.0"
ENVIRONMENT = "TEST"
UID = "aB3cD4eF5gH"


def _issues(output: metadata.CompileOutput, rule: str) -> list[Issue]:
    return [issue for issue in output.issues if issue.rule_id == rule]


def _blocking(output: metadata.CompileOutput) -> list[Issue]:
    return [issue for issue in output.issues if issue.level is IssueLevel.BLOCKING]


def _document(
    mapping_entries: Sequence[dict[str, Any]] = (),
    registry_entries: Sequence[dict[str, Any]] = (),
    *,
    sections: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    document = helpers.empty_document()
    document["mapping"]["entries"] = list(mapping_entries)
    document["registry"]["entries"] = list(registry_entries)
    for key, value in (sections or {}).items():
        document[key] = value
    return document


def _inputs(
    document: dict[str, Any],
    *,
    bindings: Mapping[str, str] | None = None,
    observed: Mapping[str, dict[str, Any]] | None = None,
    environment_key: str | None = None,
    discovery: contract.DiscoveryResult | None = None,
    deletion_candidates: Sequence[str] = (),
) -> CompileInputs:
    return CompileInputs(
        project_id=PROJECT,
        blueprint_version=VERSION,
        blueprint_hash=helpers.BASE_HASH,
        document=document,
        build_context=BuildContext(
            built_at=datetime(2026, 5, 5, tzinfo=UTC), seed=1, requester="tester"
        ),
        blueprint_artifact_id="art-01BLUEPRINT00000000000000",
        profile=contract.profile_from_json(profile_document()),
        discovery=discovery,
        environment_key=environment_key,
        bindings=dict(bindings or {}),
        observed=dict(observed or {}),
        deletion_candidates=list(deletion_candidates),
    )


def _mapping(key: str, dhis2_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    return helpers.mapping_entry(key, dhis2_type, fields=fields)


def _registry(
    key: str,
    dhis2_type: str,
    disposition: str,
    *,
    uid: str | None = UID,
    **kwargs: Any,
) -> dict[str, Any]:
    return helpers.registry_entry(key, dhis2_type, disposition, uid=uid, **kwargs)


def _text() -> dict[str, Any]:
    """The smallest dataElement mapping that satisfies its descriptor."""
    return {
        "name": {"default": "Clinic name"},
        "shortName": {"default": "Clinic"},
        "valueType": "TEXT",
        "domainType": "ATTRIBUTE",
    }


def _program_fields(**overrides: Any) -> dict[str, Any]:
    """A Program that states its required CategoryCombo, as DHIS2 demands."""
    fields: dict[str, Any] = {
        "name": {"default": "Pregnancy"},
        "shortName": {"default": "Preg"},
        "programType": "WITH_REGISTRATION",
        "categoryCombo": "default.categorycombo",
    }
    fields.update(overrides)
    return fields


# --- dispositions -----------------------------------------------------------


def test_an_external_object_is_described_but_never_compiled() -> None:
    """BP-REG-001/002: EXTERNAL is neither in the payload nor in the managed scope."""
    document = _document(
        [_mapping("facility", "organisationUnit", {"name": {"default": "Facility"}})],
        [_registry("facility", "organisationUnit", "EXTERNAL", uid=None, observed_uid="ouFacility")],
    )
    output = metadata.compile_metadata(_inputs(document))
    assert output.object_states["facility"] == "EXTERNAL"
    assert "facility" not in output.objects
    assert not _blocking(output)


def test_a_deprecated_object_leaves_the_payload_but_stays_managed() -> None:
    """BP-REG-002: DEPRECATED is managed, because the project must still retire it."""
    document = _document(
        [_mapping("oldname", "dataElement", _text())],
        [_registry("oldname", "dataElement", "DEPRECATED", observed_uid="deOldName001")],
    )
    inputs = _inputs(document, deletion_candidates=["oldname"])
    output = metadata.compile_metadata(inputs)
    assert output.object_states["oldname"] == "DEPRECATED"
    assert "oldname" not in output.objects
    assert [item["businessKey"] for item in output.deletion_candidates] == ["oldname"]
    # The row is what S4 addresses the deletion by, and it is the *only* place a DEPRECATED object
    # is listed: they leave `objects`, so the reader's businessKey back-fill cannot reach them.
    # A missing uid is not a typo either — S4 refuses an unaddressable candidate rather than
    # guessing (DX-EXE-038).  Keys here must match S4's ``execution/deletion.py`` reader field for
    # field; S1's ``tests/service/test_real_s4_deletion.py`` consumes this same shape end to end.
    row = output.deletion_candidates[0]
    assert set(row) == {"businessKey", "dhis2Type", "uid", "reason"}
    assert (row["dhis2Type"], row["uid"]) == ("dataElement", "aB3cD4eF5gH"), (
        "the address S4 deletes by comes from the binding, not from the document"
    )
    assert managed_scope.is_managed(Disposition.DEPRECATED.value)
    assert not managed_scope.is_managed(Disposition.EXTERNAL.value)


def test_a_type_with_no_enabled_descriptor_blocks_the_compile() -> None:
    """FR-COV-002: an unregistered type stops the build rather than being skipped."""
    document = _document(
        [_mapping("weekly", "reportTable", {"name": {"default": "Weekly"}})],
        [_registry("weekly", "reportTable", "CREATE")],
    )
    output = metadata.compile_metadata(_inputs(document))
    issue = _issues(output, metadata.RULE_TYPE_UNREGISTERED)
    assert issue and issue[0].business_key == "weekly"
    assert "reportTable" in issue[0].message


# --- UPDATE and the environment --------------------------------------------


def test_an_update_object_waits_for_the_environment_it_is_bound_to() -> None:
    """BP-CMP-010: a baseline compile has nothing to merge an UPDATE over."""
    document = _document(
        [_mapping("clinicname", "dataElement", _text())],
        [
            _registry(
                "clinicname", "dataElement", "UPDATE", uid=None, observed_uid="deClinic001",
                controlled_fields=["name"],
            )
        ],
    )
    output = metadata.compile_metadata(_inputs(document))
    assert "clinicname" not in output.objects
    assert "clinicname" in output.environment_dependent
    assert any("environment package" in note for note in output.notes)
    assert not _blocking(output)


def test_an_update_needs_the_environments_current_content() -> None:
    """FR-CMP-007: merging blind would overwrite fields this project does not own."""
    document = _update_document()
    inputs = _inputs(document, environment_key=ENVIRONMENT, bindings={"clinicname": "deClinic001"})
    output = metadata.compile_metadata(inputs)
    issue = _issues(output, metadata.RULE_NO_UPDATE_BASE)
    assert issue and issue[0].uid == "deClinic001"
    assert "clinicname" not in output.objects


def test_an_update_ships_only_the_fields_it_declares_as_controlled() -> None:
    document = _update_document()
    observed = {"deClinic001": {"id": "deClinic001", "name": "Facility name", "description": "kept"}}
    inputs = _inputs(
        document,
        environment_key=ENVIRONMENT,
        bindings={"clinicname": "deClinic001"},
        observed=observed,
    )
    output = metadata.compile_metadata(inputs)
    element = output.objects["clinicname"]
    assert element["name"] == "Clinic name", "the controlled field comes from the Blueprint"
    assert element["description"] == "kept", "everything else stays as the environment has it"
    assert output.object_states["clinicname"] == "UPDATED"


def _update_document() -> dict[str, Any]:
    fields = _text()
    fields["description"] = {"default": "rewritten by the project"}
    return _document(
        [_mapping("clinicname", "dataElement", fields)],
        [
            _registry(
                "clinicname", "dataElement", "UPDATE", uid=None, observed_uid="deClinic001",
                controlled_fields=["name"],
            )
        ],
    )


# --- dependencies ----------------------------------------------------------


def test_a_required_dependency_that_nothing_supplies_blocks() -> None:
    """FR-COV-003…005: an unmounted ProgramStage compiles to nothing S4 could import."""
    document = _document(
        [_mapping("intake", "programStage", {"name": {"default": "Intake"}})],
        [_registry("intake", "programStage", "CREATE")],
    )
    output = metadata.compile_metadata(_inputs(document))
    issue = _issues(output, metadata.RULE_DEPENDENCY_UNRESOLVED)
    assert issue and "program" in issue[0].message
    assert "intake" not in output.objects


def test_a_reference_that_binds_to_nothing_here_blocks_an_environment_package() -> None:
    fields = _text()
    fields["optionSet"] = "colors"
    document = _document(
        [_mapping("severity", "dataElement", fields)],
        [_registry("severity", "dataElement", "CREATE")],
    )
    inputs = _inputs(document, environment_key=ENVIRONMENT)
    output = metadata.compile_metadata(inputs)
    issue = _issues(output, metadata.RULE_DEPENDENCY_UNRESOLVED)
    assert issue and issue[0].business_key == "severity"
    assert "no object in environment" in issue[0].message


def test_an_unbound_reference_becomes_a_placeholder_that_awaits_the_environment() -> None:
    """A baseline compile names the object by businessKey and says so in the graph."""
    fields = _text()
    fields["optionSet"] = "colors"
    document = _document(
        [_mapping("severity", "dataElement", fields)],
        [_registry("severity", "dataElement", "CREATE")],
    )
    output = metadata.compile_metadata(_inputs(document))
    assert output.objects["severity"]["optionSet"] == {"id": "{{ref:colors}}"}
    assert "colors" in output.environment_dependent


def test_embedded_lists_resolve_their_items_too() -> None:
    document = _document(
        [
            {
                **_mapping(
                    "intake",
                    "programStage",
                    {"name": {"default": "Intake"}, "program": "pregnancy"},
                ),
                "structure": {
                    "programStageDataElements": [
                        {"dataElement": "clinicname", "categoryCombo": "default.categorycombo"},
                        {"dataElement": "readmission"},
                    ]
                },
            },
            _mapping("pregnancy", "program", _program_fields(programType="WITHOUT_REGISTRATION")),
        ],
        [
            _registry("intake", "programStage", "CREATE"),
            _registry("pregnancy", "program", "CREATE"),
            _registry("clinicname", "dataElement", "CREATE", uid="deClinic001"),
            _registry("default.categorycombo", "categoryCombo", "CREATE", uid="catComboDf1"),
        ],
    )
    inputs = _inputs(document, bindings={"pregnancy": UID})
    output = metadata.compile_metadata(inputs)
    assert not _blocking(output), [issue.message for issue in _blocking(output)]
    items = output.objects["intake"]["programStageDataElements"]
    assert items[0]["dataElement"] == {"id": "deClinic001"}
    assert items[1]["dataElement"] == {"id": "{{ref:readmission}}"}
    assert output.objects["intake"]["program"] == {"id": UID}


# --- implicit rules --------------------------------------------------------


def test_a_program_must_say_whether_it_registers_anything() -> None:
    """FR-COV-004."""
    document = _document(
        [_mapping("pregnancy", "program", _program_fields())],
        [
            _registry("pregnancy", "program", "CREATE"),
            _registry("default.categorycombo", "categoryCombo", "CREATE"),
        ],
    )
    output = metadata.compile_metadata(_inputs(document))
    issue = _issues(output, metadata.RULE_PROGRAM_KIND)
    assert issue and issue[0].business_key == "pregnancy"


def test_an_attribute_must_say_where_it_is_mounted() -> None:
    """FR-COV-005."""
    document = _document(
        [
            _mapping(
                "parity",
                "trackedEntityAttribute",
                {"name": {"default": "Parity"}, "valueType": "NUMBER"},
            )
        ],
        [_registry("parity", "trackedEntityAttribute", "CREATE")],
    )
    output = metadata.compile_metadata(_inputs(document))
    assert _issues(output, metadata.RULE_ATTRIBUTE_MOUNT)


def test_the_default_category_combo_is_taken_from_the_environment_and_reported() -> None:
    """FR-COV-003: an implicit dependency still shows up as a reference outside the scope."""
    document = _document(
        [_mapping("severity", "dataElement", _text())],
        [_registry("severity", "dataElement", "CREATE")],
    )
    discovery = contract.discovery_from_json(
        discovery_document(
            ENVIRONMENT,
            [discovered_object("categoryCombo", "catComboDf1", code="default", name="Default")],
            defaults={"categoryCombo": "catComboDf1"},
        )
    )
    output = metadata.compile_metadata(_inputs(document, environment_key=ENVIRONMENT, discovery=discovery))
    assert output.objects["severity"]["categoryCombo"] == {"id": "catComboDf1"}
    assert any("default CategoryCombo" in note for note in output.notes)
    reported = {
        str(item.get("uid")) for item in output.unmanaged_references
    }
    assert "catComboDf1" in reported


def test_without_a_discovery_result_the_default_combo_stays_a_placeholder() -> None:
    document = _document(
        [_mapping("severity", "dataElement", _text())],
        [_registry("severity", "dataElement", "CREATE")],
    )
    output = metadata.compile_metadata(_inputs(document))
    assert output.objects["severity"]["categoryCombo"] == {"id": "{{ref:default.categoryCombo}}"}
    assert not _blocking(output)


# --- sharing, translations, templates ------------------------------------


def test_sharing_and_its_roles_come_from_the_approved_access_model() -> None:
    """FR-COV-006: a role businessKey never ships; the environment's name does."""
    document = _document(
        [_mapping("severity", "dataElement", _text())],
        [_registry("severity", "dataElement", "CREATE")],
    )
    document["accessModel"]["roles"] = [
        {"businessKey": "mch-nurse", "name": "MCH nurse", "dataSourceScope": "all"},
        {"businessKey": "mch-supervisor", "name": "MCH supervisor", "dataSourceScope": "all"},
    ]
    document["accessModel"]["sharingDefaults"] = {
        "publicAccess": "read",
        "ownerRoles": ["mch-supervisor", "mch-nurse"],
    }
    output = metadata.compile_metadata(_inputs(document))
    sharing = output.objects["severity"]["sharing"]
    # DHIS2 2.41 reads a share as an ACL document (`public` / `users` / `userGroups`), so the
    # accessModel's vocabulary must not ship: an instance that cannot parse the block ignores
    # it entirely, keeps its own default, and POST_VERIFY then reports the approved share as
    # missing (DX-EXE-040).  What does ship keeps this test's original guarantee: the role
    # businessKey is gone and the resolved name is what the instance can match on.
    assert sharing["public"] == "r-------", "the approved 'read' share leaves as the 2.41 ACL"
    assert set(sharing["userGroups"]) == {"MCH nurse", "MCH supervisor"}, sharing
    assert all(acl == "r-------" for acl in sharing["userGroups"].values())
    assert "ownerRoles" not in sharing and "publicAccess" not in sharing


def test_translations_are_emitted_in_the_format_the_profile_declares() -> None:
    """AC-COV-02: the localised text leaves as DHIS2 wants it, not as the Blueprint holds it."""
    fields = _text()
    fields["name"] = {"default": "Clinic name", "translations": {"fr": "Nom de la clinique"}}
    document = _document(
        [_mapping("clinicname", "dataElement", fields)],
        [_registry("clinicname", "dataElement", "CREATE")],
    )
    output = metadata.compile_metadata(_inputs(document))
    element = output.objects["clinicname"]
    assert element["name"] == "Clinic name"
    assert {"field": "name", "locale": "fr", "text": "Nom de la clinique"} in element["translations"]


def test_no_template_placeholder_survives_into_a_payload_object() -> None:
    """The template is a shape, not a value: only filled fields may ship (FR-CMP-001)."""
    document = _document(
        [
            _mapping("severity", "dataElement", _text()),
            _mapping("pregnancy", "program", _program_fields(programType="WITHOUT_REGISTRATION")),
        ],
        [
            _registry("severity", "dataElement", "CREATE"),
            _registry("pregnancy", "program", "CREATE"),
            _registry("default.categorycombo", "categoryCombo", "CREATE"),
        ],
    )
    output = metadata.compile_metadata(_inputs(document, environment_key=ENVIRONMENT))
    assert not _blocking(output), [issue.message for issue in _blocking(output)]
    for business_key, payload in sorted(output.objects.items()):
        leaked = [key for key, value in payload.items() if isinstance(value, str) and "${" in value]
        assert not leaked, f"{business_key} shipped an unfilled template field: {leaked}"


def test_a_system_field_never_leaves_the_compiler() -> None:
    """FR-CMP-006: the descriptor's forbidden list is applied, whatever the Blueprint said."""
    fields = _text()
    fields["created"] = "2020-01-01"
    fields["href"] = "https://example.invalid/api/dataElements/1"
    document = _document(
        [_mapping("clinicname", "dataElement", fields)],
        [_registry("clinicname", "dataElement", "CREATE")],
    )
    output = metadata.compile_metadata(_inputs(document))
    assert "created" not in output.objects["clinicname"]
    assert "href" not in output.objects["clinicname"]


# --- supporting pure functions -------------------------------------------


def test_readme_sections_keep_their_order_without_a_clock() -> None:
    rendered = files.md_lines([("Build", ["line one", "line two"]), ("Objects", ["program"])])
    assert rendered == b"# Build\nline one\nline two\n\n## Objects\nprogram\n"


def test_the_document_decides_the_defaults_the_compilers_depend_on() -> None:
    """A sparse Blueprint still reports the project semantics the compiler reads."""
    document = helpers.empty_document()
    for key in ("project", "generationPolicy"):
        document.pop(key, None)
    inputs = _inputs(document)
    assert (inputs.delivery_mode, inputs.project_type) == ("B", "BROWNFIELD")
    assert (inputs.default_language, inputs.translation_languages) == ("en", [])
    assert inputs.generation_policy == {}
    assert inputs.settings().compiler_version


# --- BP-TD-009: the dependency graph product -----------------------------


def _graph_document() -> dict[str, Any]:
    """Three registered objects, one referencing another, one dangling, one unreferenced."""
    document = helpers.with_entries(
        helpers.empty_document(),
        "/registry/entries",
        _registry("pregnancy", "program", "CREATE"),
        _registry("dueDate", "dataElement", "CREATE"),
        _registry("unreferenced", "categoryOption", "REFERENCE"),
    )
    return helpers.with_entries(
        document,
        "/domain/entities",
        {
            "businessKey": "pregnancy",
            "displayName": {"default": "Pregnancy"},
            "attributes": [
                {"name": "dueDate", "valueType": "DATE", "optionSetKey": "dueDate"},
                # names no registered object, so it can become no edge
                {"name": "ghost", "valueType": "TEXT", "optionSetKey": "nowhere"},
                # a reference to the object holding it is not a dependency
                {"name": "self", "valueType": "TEXT", "optionSetKey": "pregnancy"},
            ],
        },
    )


def _graph() -> dict[str, Any]:
    output = metadata.CompileOutput(objects={"pregnancy": {"name": "Pregnancy"}})
    return metadata.build_graph(_inputs(_graph_document()), {}, output)


def test_the_graph_nodes_carry_the_uid_and_degrees_the_build_resolved() -> None:
    nodes = {node["businessKey"]: node for node in _graph()["nodes"]}
    assert set(nodes) == {"pregnancy", "dueDate", "unreferenced"}
    # a metadata object graph: nodes are Registry entries and nothing else (BP-TD-006),
    # so there is no section to report -- what tells a node apart is its degree
    assert "section" not in nodes["pregnancy"]
    assert nodes["pregnancy"]["uid"] == UID
    assert nodes["pregnancy"]["managed"] is True
    assert nodes["unreferenced"]["managed"] is False, "a REFERENCE object is not managed"
    assert nodes["pregnancy"]["inPayload"] is True
    assert (nodes["pregnancy"]["inDegree"], nodes["pregnancy"]["outDegree"]) == (0, 1)
    assert (nodes["dueDate"]["inDegree"], nodes["dueDate"]["outDegree"]) == (1, 0)
    assert (nodes["unreferenced"]["inDegree"], nodes["unreferenced"]["outDegree"]) == (0, 0)


def test_the_graph_lists_orphans_and_dangling_references_separately() -> None:
    """BP-AC-16: what nothing references and what does not resolve are both reported."""
    graph = _graph()
    assert graph["orphans"] == ["unreferenced"]
    assert graph["unresolvedRegistryRefs"] == [
        {"from_business_key": "pregnancy", "field": "optionSetKey", "target": "nowhere"}
    ], "a self-reference is not a dangling one"
    edges = [(e["from_business_key"], e["field"], e["to_business_key"]) for e in graph["edges"]]
    assert edges == [("pregnancy", "optionSetKey", "dueDate")]


def test_the_graph_states_its_format_and_is_the_same_bytes_twice() -> None:
    """BP-TD-009 / BP-CMP-001: the reader must tell an old file from an empty answer."""
    graph = _graph()
    assert graph["schemaVersion"] == metadata.GRAPH_SCHEMA_VERSION
    assert files.json_bytes(graph) == files.json_bytes(_graph())


def test_a_binding_replaces_the_pooled_uid_in_a_graph_node() -> None:
    """An environment-bound build answers with the UID that environment resolved."""
    node = next(
        item
        for item in metadata.build_graph(
            _inputs(_graph_document()), {"dueDate": "BIND1"}, metadata.CompileOutput()
        )["nodes"]
        if item["businessKey"] == "dueDate"
    )
    assert node["uid"] == "BIND1"
