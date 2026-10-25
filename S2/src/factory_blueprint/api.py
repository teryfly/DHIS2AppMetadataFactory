"""``factory_blueprint.api`` — the single public entry point of S2 (00 doc 6.1, D-13).

Other subsystems import only from this module.  It re-exports the functions of the
SRS chapter 7 table, grouped as in that table, plus the DTOs a caller needs to read
their results.  Nothing here contains logic: every name is a service function, and
the "类别" column of the SRS table is preserved by the decorators on those functions —
a function marked external I/O raises ``TRANSACTION_MISUSE`` if S1 calls it inside its
unit of work, which is how the transaction boundary stays a fact rather than a rule
written in prose.

Deviations from the SRS table, both forced by S0:
  * ``prepare_draft_base`` (7.3) materializes a draft's base content.  ``open_draft``
    and ``submit_patch`` are transactional, and reading an artifact inside a
    transaction is forbidden, so the read gets its own external-I/O step — the same
    reason the SRS gives for BP-DRF-008.
  * ``get_entries`` and ``analyze_impact`` (7.2, 7.4) are external I/O, not plain
    reads, because they answer with entry contents, which live in the version artifact.
    ``locate_entries`` is the transaction-safe half of the same question.
  * the package reads take ``artifact_id``.  S1 and S4 already use ``package_artifact_id``
    for the ``METADATA_PACKAGE`` handed to S4 for a commit, while BP-HO-004 means the
    *development* package; chapter 7.5 adopted this name in SRS V1.1.3.  The accepted types
    are listed in ``artifact_reads.LISTABLE_PACKAGE_TYPES``.
"""

from __future__ import annotations

from factory_blueprint.artifact_reads import (
    get_dependency_graph,
    list_package_files,
    read_package_file,
)
from factory_blueprint.bootstrap import MIGRATION_CONFIG, initialize
from factory_blueprint.build.service import (
    compile_baseline,
    compile_for_environment,
    generate_frontend_package,
    reproduce,
    validate_build,
)
from factory_blueprint.config import BlueprintSettings, configure, settings
from factory_blueprint.deps import get_dhis2, reset_providers, set_dhis2
from factory_blueprint.descriptors.registry import catalog as list_object_types
from factory_blueprint.diff.service import diff_draft, diff_versions
from factory_blueprint.draft.service import (
    commit_mode_upgrade,
    freeze_draft,
    get_active_draft,
    get_draft,
    open_draft,
    prepare_draft_base,
    prepare_initial_blueprint,
    prepare_mode_upgrade,
    promote_draft,
    register_initial_blueprint,
    reset_draft,
    submit_patch,
)
from factory_blueprint.dto.blueprint import (
    CurrentVersion,
    EntrySelector,
    InitialProjectInfo,
    ProjectSemantics,
    SectionCatalog,
    SectionInfo,
    SelectedEntries,
    VersionInfo,
)
from factory_blueprint.dto.build import BuildContext, BuildInput, BuildResult, ReproduceResult
from factory_blueprint.dto.diff import DiffReport, FieldChange, ObjectDiff
from factory_blueprint.dto.graph import (
    DependencyGraph,
    GraphFilters,
    GraphNode,
    ObjectRelation,
    UnresolvedGraphReference,
)
from factory_blueprint.dto.issue import Issue
from factory_blueprint.dto.manifest import PackageFile, PackageManifest
from factory_blueprint.dto.package_files import (
    PACKAGE_FILE_KINDS,
    PackageFileContent,
    PackageFileEntry,
    PackageFileListing,
)
from factory_blueprint.dto.patch import (
    DraftInfo,
    FreezeResult,
    ImpactSet,
    MergeQuestionData,
    Patch,
    PatchAuthor,
    PatchContext,
    PatchOutcome,
    PromoteResult,
)
from factory_blueprint.dto.portability import (
    ExportResult,
    ImportPreview,
    ImportResult,
    ProjectCleanupResult,
)
from factory_blueprint.dto.registry import (
    BindingInfo,
    DecisionInfo,
    ManagedObject,
    OwnershipConflict,
    RegistryEntry,
    UidPoolStatus,
)
from factory_blueprint.enums import (
    AuthorKind,
    BindingResolution,
    BuildKind,
    BuildStatus,
    ChangeLevel,
    DeliveryMode,
    DesignStage,
    Disposition,
    DraftStatus,
    ImpactLevel,
    IssueLevel,
    PatchStatus,
    ProjectType,
    RegistryEntryStatus,
    Section,
    Tier,
    UidAllocationStatus,
    VersionStatus,
)
from factory_blueprint.errors import (
    BlueprintError,
    DescriptorMissing,
    DraftNotMaterialized,
    ExpressionGrammarMissing,
    FeatureNotEnabled,
    ImportConflict,
    NotPromotable,
    OperationInProgress,
    RegistryError,
    SchemaVersionUnsupported,
    UidPoolExhausted,
)
from factory_blueprint.portability.cleanup import delete_project_data
from factory_blueprint.portability.export import export_blueprint
from factory_blueprint.portability.import_ import commit_import, prepare_import
from factory_blueprint.readmodel import (
    analyze_impact,
    get_bindings,
    get_current_version,
    get_decisions,
    get_entries,
    get_managed_scope,
    get_object_relationships,
    get_package_manifest,
    get_project_semantics,
    get_registry,
    get_uid_pool_status,
    get_version,
    list_versions,
    locate_entries,
    set_environment_binding,
)
from factory_blueprint.registry.pool import replenish
from factory_blueprint.s4.adapter import S4Adapter
from factory_blueprint.s4.protocol import CodeOccupancy, ReadonlyDhis2, UidOccupancy
from factory_blueprint.s4.stub import StubDhis2
from factory_blueprint.schema.catalog import list_sections
from factory_blueprint.schema.migrations import (
    has_migration_path,
    migrate_document,
    register_migration,
)
from factory_blueprint.schema.store import (
    available_versions,
    current_schema_version,
    document_schema,
    section_schema,
)
from factory_blueprint.validators.query import query_issues

get_section_schema = section_schema
replenish_uid_pool = replenish
#: chapter 7.4 calls the binding read ``get_environment_bindings``; S1 resolves the
#: shorter ``get_bindings``, so both names mean the same function
get_environment_bindings = get_bindings


__all__ = [
    # 7.1 initialization and schema
    "initialize",
    "MIGRATION_CONFIG",
    "list_sections",
    "list_object_types",
    "SectionCatalog",
    "SectionInfo",
    "get_section_schema",
    "section_schema",
    "document_schema",
    "current_schema_version",
    "available_versions",
    "migrate_document",
    "register_migration",
    "has_migration_path",
    "prepare_initial_blueprint",
    "register_initial_blueprint",
    "InitialProjectInfo",
    # 7.2 versions and queries
    "get_current_version",
    "CurrentVersion",
    "list_versions",
    "get_version",
    "VersionInfo",
    "get_project_semantics",
    "ProjectSemantics",
    "get_entries",
    "locate_entries",
    "EntrySelector",
    "SelectedEntries",
    "get_decisions",
    "DecisionInfo",
    "diff_versions",
    "diff_draft",
    "DiffReport",
    "ObjectDiff",
    "FieldChange",
    # 7.3 drafts and patches
    "open_draft",
    "get_draft",
    "get_active_draft",
    "prepare_draft_base",
    "submit_patch",
    "Patch",
    "PatchAuthor",
    "PatchContext",
    "PatchOutcome",
    "MergeQuestionData",
    "DraftInfo",
    "reset_draft",
    "freeze_draft",
    "FreezeResult",
    "promote_draft",
    "PromoteResult",
    "ImpactSet",
    "prepare_mode_upgrade",
    "commit_mode_upgrade",
    # 7.4 impact and registry
    "analyze_impact",
    "get_registry",
    "RegistryEntry",
    # the dependency graph's edges; its nodes are the Registry above (BP-IMP-004)
    "get_object_relationships",
    "ObjectRelation",
    "get_managed_scope",
    "ManagedObject",
    "get_bindings",
    "get_environment_bindings",
    "BindingInfo",
    "replenish_uid_pool",
    "UidPoolStatus",
    "get_uid_pool_status",
    "set_environment_binding",
    "OwnershipConflict",
    # 7.5 compile, validate, reproduce
    "BuildContext",
    "BuildInput",
    "BuildResult",
    "compile_baseline",
    "compile_for_environment",
    "get_package_manifest",
    "PackageFile",
    "PackageManifest",
    "validate_build",
    "reproduce",
    "ReproduceResult",
    "Issue",
    "query_issues",
    "generate_frontend_package",
    # reading a build's products back: the graph and the development package's files
    "get_dependency_graph",
    "DependencyGraph",
    "GraphNode",
    "GraphFilters",
    "UnresolvedGraphReference",
    "list_package_files",
    "read_package_file",
    "PackageFileEntry",
    "PackageFileListing",
    "PackageFileContent",
    # the enumeration a caller declares in its own contract (00 doc 6.11)
    "PACKAGE_FILE_KINDS",
    # 7.6 export, import, cleanup
    "export_blueprint",
    "ExportResult",
    "prepare_import",
    "ImportPreview",
    "commit_import",
    "ImportResult",
    "delete_project_data",
    "ProjectCleanupResult",
    # the read-only DHIS2 collaborator: S4Adapter is the default, StubDhis2 what a test installs
    "ReadonlyDhis2",
    "S4Adapter",
    "StubDhis2",
    "UidOccupancy",
    "CodeOccupancy",
    "set_dhis2",
    "get_dhis2",
    "reset_providers",
    # configuration and enums
    "BlueprintSettings",
    "settings",
    "configure",
    "AuthorKind",
    "BindingResolution",
    "BuildKind",
    "BuildStatus",
    "ChangeLevel",
    "DeliveryMode",
    "DesignStage",
    "Disposition",
    "DraftStatus",
    "ImpactLevel",
    "IssueLevel",
    "PatchStatus",
    "ProjectType",
    "RegistryEntryStatus",
    "Section",
    "Tier",
    "UidAllocationStatus",
    "VersionStatus",
    # errors
    "BlueprintError",
    "DescriptorMissing",
    "DraftNotMaterialized",
    "ExpressionGrammarMissing",
    "FeatureNotEnabled",
    "ImportConflict",
    "NotPromotable",
    "OperationInProgress",
    "RegistryError",
    "SchemaVersionUnsupported",
    "UidPoolExhausted",
]
