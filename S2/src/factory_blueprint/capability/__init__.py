"""What one environment's Capability Profile says about a compilation.

FR-CAP-001 requires that a new DHIS2 version be added as data, never as compiler or
validator code, so every "does this version have it" question the compilers ask is
answered here from the Profile the caller was given (P-10: capability over
assumption).  The Profile is an input, so nothing in this package decides anything on
its own: it reads, compares and reports (C-07).
"""

from __future__ import annotations

from factory_blueprint.capability.language import (
    ADDRESSING_FIELDS,
    DEPRECATED,
    FORBIDDEN,
    REMOVED,
    SUPPORTED,
)
from factory_blueprint.capability.view import (
    BASELINE_SHARING_FORMAT,
    BASELINE_TRANSLATION_FORMAT,
    ProfileView,
    series,
    view_of,
)

__all__ = [
    "ADDRESSING_FIELDS",
    "BASELINE_SHARING_FORMAT",
    "BASELINE_TRANSLATION_FORMAT",
    "DEPRECATED",
    "FORBIDDEN",
    "REMOVED",
    "SUPPORTED",
    "ProfileView",
    "series",
    "view_of",
]
