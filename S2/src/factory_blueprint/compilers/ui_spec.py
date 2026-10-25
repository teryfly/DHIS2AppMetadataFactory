"""UI Spec Compiler (BP-CMP-042, overall SRS 10.3 / 12.1).

Input is `pages` + `uiIntents` + `apiIntents` + `registry`; output is the machine-readable
UI Specification and one Markdown page spec per page.

The Blueprint already holds every element 10.3 requires — the section schema enforces the
component list, the ten page states, field mappings, program rules, translation keys and
test scenarios — so this compiler's job is *projection and enrichment*, not invention:

* a field mapping is enriched with the DHIS2 object it addresses, so a frontend
  developer reads `valueType: NUMBER` and the UID rather than a businessKey alone
  (FR-FE-001);
* each component is marked `@dhis2/ui` or `custom` (FR-UII-001).  That is an annotation,
  not a rule: the Capability Profile carries no component catalogue, so refusing a name
  S2 cannot verify would be guessing (P-10).  A reviewer sees which components still
  have to be mapped onto the design system.

Consistency of a component's data source with the page's API intents is *not* checked
here — `validators.cross_artifact` owns FR-UII-002, and a second copy of that rule would
give one defect two rule IDs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from factory_blueprint.compilers import files
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.refs import placeholder
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.issue import Issue

PAGES_CONTAINER = CONTAINER_BY_PATH["/pages/items"]
UI_INTENTS_CONTAINER = CONTAINER_BY_PATH["/uiIntents/items"]
API_INTENTS_CONTAINER = CONTAINER_BY_PATH["/apiIntents/items"]
REGISTRY_CONTAINER = CONTAINER_BY_PATH["/registry/entries"]

UI_SPEC_FILE = "ui/ui-spec.json"

_T = TypeVar("_T")

#: the every-page state machine 10.3 fixes; a page missing one is shown as not declaring it
PAGE_STATES: tuple[str, ...] = (
    "INITIAL",
    "LOADING",
    "READY",
    "EMPTY",
    "SUBMITTING",
    "SUCCESS",
    "VALIDATION_ERROR",
    "API_ERROR",
    "PERMISSION_DENIED",
    "NETWORK_ERROR",
)

#: names that exist in the DHIS2 design system.  Deliberately a *recognition* list: a
#: component outside it is reported as custom, never as an error, because the version's
#: real catalogue is not something the Capability Profile tells S2.
DHIS2_UI_COMPONENTS = frozenset(
    {
        "Button",
        "ButtonStrip",
        "Calendar",
        "Card",
        "Checkbox",
        "Chip",
        "CircledIcon",
        "CollapsibleCard",
        "CssTransition",
        "DataGrouping",
        "DataTable",
        "DatePicker",
        "Divider",
        "DocumentTitle",
        "FileInput",
        "Field",
        "FieldGroup",
        "FilledCard",
        "Icon",
        "Input",
        "Intent",
        "Layer",
        "HeaderBar",
        "LoadingIndicator",
        "Mat",
        "Menu",
        "MenuItem",
        "MenuDivider",
        "Modal",
        "ModalHeader",
        "NoticeBox",
        "NumberInput",
        "Pager",
        "Popover",
        "Radio",
        "SegmentedControl",
        "Selector",
        "Center",
        "Stacked",
        "Row",
        "Column",
        "Group",
        "Switch",
        "Tab",
        "Tag",
        "TextArea",
    }
)

#: the components that are actually data-entry controls, for the field-mapping view
INPUT_COMPONENTS = frozenset(
    {"Input", "TextArea", "NumberInput", "DatePicker", "Calendar", "Checkbox", "Radio", "Switch", "FileInput"}
)


@dataclass
class UiSpecProduct:
    """The UI Specification document, the page Markdown and the problems found."""

    document: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(issue.level.value == "BLOCKING" for issue in self.issues)


def compile_ui_spec(
    inputs: CompileInputs, resolved_operations: Iterable[str] | None = None
) -> UiSpecProduct:
    """Project the approved `pages` and `uiIntents` into the UI Specification (10.3).

    ``resolved_operations`` is the set of operationKeys the OpenAPI compiler actually
    produced an operation for.  Naming only those keeps a page whose single API intent
    failed to resolve from looking covered in the spec — the distinction 17.3's first
    check depends on.  Passing None means nothing was compiled against, so every
    declared operation is reported as unresolved.
    """
    document = BlueprintDocument(inputs.document)
    registry = _registry_map(document)
    operations = _operation_index(document)
    intents = {
        str(intent.get("pageKey") or ""): intent
        for intent in sorted(
            document.container_entries(UI_INTENTS_CONTAINER), key=lambda item: str(item.get("pageKey") or "")
        )
    }
    pages = sorted(
        document.container_entries(PAGES_CONTAINER), key=lambda item: str(item.get("pageKey") or "")
    )

    resolved = set(resolved_operations or ())
    compiled: dict[str, dict[str, Any]] = {}
    for page in pages:
        page_key = str(page.get("pageKey") or "")
        if not page_key:
            continue
        compiled[page_key] = _compile_page(
            inputs, page, intents.get(page_key), registry, operations, resolved
        )

    spec = _pruned(
        {
            "schemaVersion": 1,
            "kind": "UI_SPECIFICATION",
            "projectId": inputs.project_id,
            "blueprintVersion": inputs.blueprint_version,
            "blueprintHash": inputs.blueprint_hash,
            "dhis2Version": inputs.dhis2_version or None,
            "componentLibrary": "@dhis2/ui",
            "pageStates": list(PAGE_STATES),
            "pages": [compiled[key] for key in sorted(compiled)],
        }
    )
    product = UiSpecProduct(document=spec, pages=compiled)
    product.files = {
        UI_SPEC_FILE: files.json_bytes(spec),
        **{
            f"{files.PAGES_PREFIX}{key}.md": _page_markdown(inputs, compiled[key])
            for key in sorted(compiled)
        },
    }
    return product


def _compile_page(
    inputs: CompileInputs,
    page: dict[str, Any],
    intent: dict[str, Any] | None,
    registry: dict[str, dict[str, Any]],
    operations: dict[str, list[dict[str, Any]]],
    resolved: set[str],
) -> dict[str, Any]:
    page_key = str(page.get("pageKey") or "")
    declared = sorted({str(item) for item in ((intent or {}).get("states") or [])})
    return _pruned(
        {
            "pageKey": page_key,
            "name": _text(page.get("name")),
            "route": _text((intent or {}).get("route")) or _first(page.get("routes")),
            "routes": sorted({str(item) for item in (page.get("routes") or [])}),
            "purpose": _text(page.get("purpose")),
            "useCases": sorted({str(item) for item in (page.get("useCases") or [])}),
            "actors": sorted({str(item) for item in (page.get("actors") or [])}),
            "layout": [str(item) for item in ((intent or {}).get("layout") or [])],
            "components": _components(intent, page_key, operations, registry),
            # 10.3 fixes the state list, so a page that omits one is incomplete on its face
            "states": declared,
            "missingStates": [state for state in PAGE_STATES if state not in declared],
            "interactions": [str(item) for item in ((intent or {}).get("interactions") or [])],
            "navigation": _navigation(page),
            "fieldMappings": _field_mappings(intent, registry),
            "programRules": _program_rules(intent, registry),
            "translationKeys": sorted({str(item) for item in ((intent or {}).get("translationKeys") or [])}),
            "testScenarios": [str(item) for item in ((intent or {}).get("testScenarios") or [])],
            "acceptanceCriteria": [str(item) for item in (page.get("acceptanceCriteria") or [])],
            "apiOperations": sorted(
                str(operation.get("operationKey") or "")
                for operation in operations.get(page_key, [])
                if str(operation.get("operationKey") or "") in resolved
            ),
            # declared but never compiled: the page asks for data no operation returns
            "unresolvedOperations": sorted(
                str(operation.get("operationKey") or "")
                for operation in operations.get(page_key, [])
                if str(operation.get("operationKey") or "") not in resolved
            ),
            "uiIntentRef": _text(page.get("uiIntentRef")),
            "registryRefs": _page_registry_refs(page, registry),
            "hasUiIntent": intent is not None,
        }
    )


def _components(
    intent: dict[str, Any] | None,
    page_key: str,
    operations: dict[str, list[dict[str, Any]]],
    registry: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    owned = {operation["operationKey"] for operation in operations.get(page_key, [])}
    out: list[dict[str, Any]] = []
    for component in (intent or {}).get("components") or []:
        if not isinstance(component, dict):
            continue
        name = str(component.get("name") or "")
        source = component.get("dataSource") or {}
        entry: dict[str, Any] = {
            "name": name,
            "library": "@dhis2/ui" if name.split(".")[0] in DHIS2_UI_COMPONENTS else "custom",
            "use": _text(component.get("use")),
            "props": _pruned(component.get("props") or {}),
            "events": sorted({str(item) for item in (component.get("events") or [])}),
            "validation": sorted({str(item) for item in (component.get("validation") or [])}),
            "visibleWhen": _visible_when(component.get("visibleWhen")),
            "accessibility": _text(component.get("accessibility")),
            "isInput": name in INPUT_COMPONENTS,
        }
        if isinstance(source, dict) and source:
            entry["dataSource"] = _data_source(source, owned, registry)
        out.append(_pruned(entry))
    return sorted(out, key=lambda item: (str(item.get("name")), str(item.get("use"))))


def _data_source(
    source: dict[str, Any], owned: set[str], registry: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Name the operation or object a component reads, and whether S2 can see it here."""
    operation_key = source.get("operationKey")
    if isinstance(operation_key, str) and operation_key:
        return {
            "type": "operation",
            "operationKey": operation_key,
            "declaredOnThisPage": operation_key in owned,
        }
    business_key = source.get("businessKey")
    if isinstance(business_key, str) and business_key:
        return {
            "type": "registry",
            "businessKey": business_key,
            "registered": business_key in registry,
        }
    return {"type": "none"}


def _visible_when(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted({str(item) for item in value})
    if isinstance(value, str) and value:
        return [value]
    return []


def _field_mappings(
    intent: dict[str, Any] | None, registry: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Each form field plus the DHIS2 object behind it (10.3, FR-FE-001)."""
    out: list[dict[str, Any]] = []
    for mapping in (intent or {}).get("fieldMappings") or []:
        if not isinstance(mapping, dict):
            continue
        business_key = str(mapping.get("businessKey") or "")
        entry = registry.get(business_key) or {}
        option_set_key = str(mapping.get("optionSetKey") or "")
        option_set = registry.get(option_set_key) or {}
        out.append(
            _pruned(
                {
                    "field": str(mapping.get("field") or ""),
                    "label": _pruned(mapping.get("label") or {}),
                    "businessKey": business_key,
                    "dhis2Type": _text(entry.get("dhis2Type")),
                    # the UID this environment resolves to, or the placeholder that says
                    # it is unresolved, so no spec claims a number S2 has not bound
                    "uid": _ref_uid(entry, unresolved=business_key),
                    "valueType": _text(mapping.get("valueType")),
                    "optionSetKey": option_set_key or None,
                    "optionSetUid": _ref_uid(option_set, unresolved=option_set_key),
                    "required": bool(mapping.get("required")),
                    "validation": sorted({str(item) for item in (mapping.get("validation") or [])}),
                    "registered": bool(entry),
                }
            )
        )
    return sorted(out, key=lambda item: str(item.get("field")))


def _program_rules(intent: dict[str, Any] | None, registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The rules a page must reproduce or expect from the server (10.3)."""
    out: list[dict[str, Any]] = []
    for key in sorted({str(item) for item in ((intent or {}).get("programRules") or [])}):
        entry = registry.get(key) or {}
        out.append(
            _pruned(
                {
                    "businessKey": key,
                    "dhis2Type": _text(entry.get("dhis2Type")),
                    "uid": _ref_uid(entry, unresolved=key),
                    "registered": bool(entry),
                }
            )
        )
    return out


def _navigation(page: dict[str, Any]) -> dict[str, Any]:
    navigation = page.get("navigation")
    if not isinstance(navigation, dict):
        return {}
    return _pruned(
        {
            "from": sorted({str(item) for item in _as_list(navigation.get("from"))}),
            "to": sorted({str(item) for item in _as_list(navigation.get("to"))}),
        }
    )


def _page_registry_refs(page: dict[str, Any], registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _pruned(
            {
                "businessKey": key,
                "dhis2Type": _text((registry.get(key) or {}).get("dhis2Type")),
                "uid": _ref_uid(registry.get(key) or {}),
            }
        )
        for key in sorted({str(item) for item in (page.get("registryRefs") or [])})
    ]


# --- page Markdown --------------------------------------------------------


def _page_markdown(inputs: CompileInputs, page: dict[str, Any]) -> bytes:
    page_key = str(page.get("pageKey") or "")
    sections: list[tuple[str, list[str]]] = []
    head = [
        f"Route: `{page.get('route') or 'not declared'}`",
        f"Purpose: {page.get('purpose') or 'not stated'}",
        f"Blueprint version: {inputs.blueprint_version} (`{inputs.blueprint_hash}`)",
        f"Target DHIS2 version: {inputs.dhis2_version or 'unbound (baseline compile)'}",
        "",
        "This page spec is generated from the approved UI Intent. Do not edit it here;",
        "change the Blueprint and recompile.",
    ]
    sections.append((f"Page `{page_key}`", head))

    if page.get("layout"):
        sections.append(("Layout", [f"- {item}" for item in page["layout"]]))

    if page.get("components"):
        lines = [
            "| component | library | purpose | data source | events | validation | a11y |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for component in page["components"]:
            lines.append(_component_row(component))
        sections.append(("Components", lines))

    declared = set(page.get("states") or [])
    state_lines = [f"- `{state}`: declared" if state in declared else f"- `{state}`: **not declared**" for state in PAGE_STATES]
    sections.append(("Page states", state_lines))

    if page.get("interactions"):
        sections.append(("Interactions", [f"- {item}" for item in page["interactions"]]))
    navigation = page.get("navigation")
    if isinstance(navigation, dict) and navigation:
        sections.append(
            ("Navigation", [f"- {key}: {', '.join(navigation[key])}" for key in sorted(navigation)])
        )
    if page.get("fieldMappings"):
        lines = [
            "| field | registry entry | type | uid | value type | option set | required | validation |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for mapping in page["fieldMappings"]:
            lines.append(_mapping_row(mapping))
        sections.append(("Field mappings", lines))
    if page.get("programRules"):
        sections.append(
            (
                "Program rules this page must honour",
                [
                    f"- `{rule['businessKey']}` ({rule.get('dhis2Type') or 'unregistered'}) "
                    f"→ `{rule.get('uid') or 'unbound'}`"
                    for rule in page["programRules"]
                ],
            )
        )
    if page.get("apiOperations"):
        sections.append(("API operations", [f"- `{item}`" for item in page["apiOperations"]]))
    if page.get("unresolvedOperations"):
        sections.append(
            (
                "API operations that did not compile",
                [
                    f"- `{item}` — declared for this page but resolved to no endpoint, so "
                    "the page has no data source for it"
                    for item in page["unresolvedOperations"]
                ],
            )
        )
    if page.get("translationKeys"):
        sections.append(("Translation keys", [f"- `{item}`" for item in page["translationKeys"]]))
    if page.get("testScenarios"):
        sections.append(("Test scenarios", [f"- {item}" for item in page["testScenarios"]]))
    if page.get("acceptanceCriteria"):
        sections.append(("Acceptance criteria", [f"- [ ] {item}" for item in page["acceptanceCriteria"]]))
    if not page.get("hasUiIntent"):
        sections.append(
            ("Missing UI Intent", ["No UI Intent is recorded for this page, so the components,"
                                   " states and field mappings above are absent rather than empty."])
        )
    return files.md_lines(sections)


def _component_row(component: dict[str, Any]) -> str:
    source = component.get("dataSource") or {}
    label = _data_source_label(source)
    return (
        f"| `{component.get('name')}` | {component.get('library')} | {component.get('use') or ''} "
        f"| {label} | {', '.join(f'`{item}`' for item in component.get('events') or [])} "
        f"| {', '.join(component.get('validation') or [])} | {component.get('accessibility') or ''} |"
    )


def _mapping_row(mapping: dict[str, Any]) -> str:
    option_set = mapping.get("optionSetKey") or ""
    return (
        f"| `{mapping.get('field')}` | `{mapping.get('businessKey')}` "
        f"| {mapping.get('dhis2Type') or 'unregistered'} | `{mapping.get('uid') or 'unbound'}` "
        f"| {mapping.get('valueType') or ''} | `{option_set}` | "
        f"{'yes' if mapping.get('required') else 'no'} "
        f"| {', '.join(mapping.get('validation') or [])} |"
    )


def _data_source_label(source: dict[str, Any]) -> str:
    kind = source.get("type")
    if kind == "operation":
        flag = "" if source.get("declaredOnThisPage") else " *(no such operationKey on this page)*"
        return f"operation `{source.get('operationKey')}`{flag}"
    if kind == "registry":
        flag = "" if source.get("registered") else " *(not in the Registry)*"
        return f"registry `{source.get('businessKey')}`{flag}"
    return ""


# --- indexes and small helpers -------------------------------------------


def _registry_map(document: BlueprintDocument) -> dict[str, dict[str, Any]]:
    return {
        str(entry["businessKey"]): entry
        for entry in document.container_entries(REGISTRY_CONTAINER)
        if isinstance(entry.get("businessKey"), str)
    }


def _operation_index(document: BlueprintDocument) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for intent in document.container_entries(API_INTENTS_CONTAINER):
        if isinstance(intent.get("pageKey"), str) and isinstance(intent.get("operationKey"), str):
            out.setdefault(intent["pageKey"], []).append(intent)
    return out


def _ref_uid(entry: dict[str, Any], *, unresolved: str | None = None) -> str:
    """The number this entry means, or a placeholder that names what is still unresolved.

    An entry with no number at all is reported as ``{{ref:<businessKey>}}`` rather than
    as a missing key: the same shape a resolved entry has, so a reader cannot mistake
    "not bound yet" for "nothing to look up".
    """
    key = str(entry.get("businessKey") or unresolved or "")
    if not key:
        return ""
    uid = entry.get("uid") or entry.get("observedUid")
    return str(uid) if uid else placeholder(key)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("default") or "")
    return "" if value is None else str(value)


def _first(value: Any) -> str:
    items = _as_list(value)
    return str(items[0]) if items else ""


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


REQUIRED_KEYS = frozenset({"required", "default"})


def _pruned(document: _T) -> _T:
    """Drop empty keys, keeping the value's own type so callers stay typed."""
    if isinstance(document, dict):
        kept = {key: _pruned(value) for key, value in document.items()}
        return cast(
            _T,
            {
                key: value
                for key, value in sorted(kept.items())
                if value not in (None, {}, [], "") or key in REQUIRED_KEYS
            },
        )
    if isinstance(document, list):
        return cast(_T, [_pruned(item) for item in document])
    return document
