"""Compiled-package DTOs (S2 SRS 7.5, BP-CMP-021/023).

The package's file list is what the metadata review's *by file* view shows (S5 FR-UI-007): which
files a build produced, their hashes, and how many objects each file carries.  Only the list is
answered here — an object's identity, type and disposition stay the Registry's answer, so the two
views of the same build cannot drift apart.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: how a file came to be in the package; the UI groups on it rather than on a name pattern
ROLE_PAYLOAD = "payload"
ROLE_MANIFEST = "manifest"
ROLE_GRAPH = "graph"
ROLE_REPORT = "report"
ROLE_VIEW = "view"
ROLE_DOCUMENT = "document"

FILE_ROLES = (ROLE_PAYLOAD, ROLE_MANIFEST, ROLE_GRAPH, ROLE_REPORT, ROLE_VIEW, ROLE_DOCUMENT)


class PackageFile(BaseModel):
    """One file of a compiled package.

    ``object_count`` is filled only for the files that carry objects (the payload and the per-type
    views); a manifest or a README holds no object, and zero would read as "this package is empty".
    """

    model_config = ConfigDict(frozen=True)

    path: str
    role: str = ROLE_DOCUMENT
    content_hash: str | None = None
    dhis2_type: str | None = None
    object_count: int | None = None


class PackageManifest(BaseModel):
    """What one build of one version put into the metadata package."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    blueprint_version: str
    build_id: str
    kind: str
    environment_key: str | None = None
    package_hash: str | None = None
    files: list[PackageFile] = Field(default_factory=list)
