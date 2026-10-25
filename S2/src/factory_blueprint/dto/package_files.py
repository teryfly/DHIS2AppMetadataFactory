"""Development-package file DTOs (S2 SRS 7.5, BP-HO-004, overall SRS 17.1).

The dev package is a ZIP this subsystem packs, and its internal layout belongs to S2: a caller
asks for a listing and for one file's content rather than unzipping the artifact itself.  These
are the two answers.

``kind`` is the file's role in the package (overall SRS 17.1), which is what a UI groups on --
it is deliberately not ``dto/manifest.py``'s ``role``, which classifies the files of a *metadata*
package by the part they play in one import.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: the roles a dev-package file can play; ``compilers/package.py::package_file_kind`` decides
PACKAGE_FILE_KIND_DOCUMENT = "DOCUMENT"
PACKAGE_FILE_KIND_CONTRACT = "CONTRACT"
PACKAGE_FILE_KIND_UI_SPEC = "UI_SPEC"
PACKAGE_FILE_KIND_REGISTRY = "REGISTRY"
PACKAGE_FILE_KIND_EXAMPLE = "EXAMPLE"
PACKAGE_FILE_KIND_FIXTURE = "FIXTURE"
PACKAGE_FILE_KIND_I18N = "I18N"
PACKAGE_FILE_KIND_TRACEABILITY = "TRACEABILITY"

PACKAGE_FILE_KINDS = (
    PACKAGE_FILE_KIND_DOCUMENT,
    PACKAGE_FILE_KIND_CONTRACT,
    PACKAGE_FILE_KIND_UI_SPEC,
    PACKAGE_FILE_KIND_REGISTRY,
    PACKAGE_FILE_KIND_EXAMPLE,
    PACKAGE_FILE_KIND_FIXTURE,
    PACKAGE_FILE_KIND_I18N,
    PACKAGE_FILE_KIND_TRACEABILITY,
)


class PackageFileEntry(BaseModel):
    """One file inside a development package, as the listing describes it.

    ``size_bytes`` is the uncompressed content length and ``content_hash`` the hash of those same
    bytes, so a caller that fetches the content can check it against the listing (BP-AC-18).
    """

    model_config = ConfigDict(frozen=True)

    path: str
    kind: str = PACKAGE_FILE_KIND_DOCUMENT
    size_bytes: int = 0
    content_hash: str = ""


class PackageFileListing(BaseModel):
    """What one development package holds."""

    model_config = ConfigDict(frozen=True)

    artifact_id: str
    project_id: str
    blueprint_version: str
    files: list[PackageFileEntry] = Field(default_factory=list)


class PackageFileContent(BaseModel):
    """One file's content.

    Every file the Package Compiler writes is UTF-8 text (Markdown, JSON or YAML), so the content
    travels as text and ``media_type`` says which of the three the caller is holding.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    kind: str = PACKAGE_FILE_KIND_DOCUMENT
    content: str
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    content_hash: str = ""
