"""Type descriptors and compile templates (BP-TD-*, FR-COV-001)."""

from factory_blueprint.descriptors import registry
from factory_blueprint.descriptors.model import (
    Dependency,
    Implicit,
    TypeDescriptor,
)

__all__ = ["Dependency", "Implicit", "TypeDescriptor", "registry"]
