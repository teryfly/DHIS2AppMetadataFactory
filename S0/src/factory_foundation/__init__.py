"""factory_foundation — S0 公共基础库 (Foundation).

Public surface lives in :mod:`factory_foundation.api`; import everything from
there.  This top-level module only exposes version metadata and lazy re-exports
so that ``import factory_foundation`` stays cheap.
"""

from __future__ import annotations

__version__ = "0.1.2"

__all__ = ["__version__", "api", "testing"]
